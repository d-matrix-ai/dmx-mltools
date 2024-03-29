import re
import torch
from types import SimpleNamespace
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, List, Optional, Union, Sequence
from mltools import dmx
from mltools.dmx.nn import *
from mltools.fx.transform import substitute_transform


class Model(torch.nn.Module):
    r"""
    Container for a DNN model to be deployed
    - body to be mapped on device
    - head and tail to be executed on host, corresponding to pre- and post-processing
    - equipped with dmx-aware transformation
    Inherited from torch.nn.Module.

    Args:
        body (Any): the main module of the model.
        head (Optional[Any]): preprocessing module before main module. Defaults to torch.nn.Identity.
        tail (Optional[Any]): postprocessing module after main module. Defaults to torch.nn.Identity.
        hf (Optional[bool]): If true, body would be treated as a huggingface model for fx transformation.

    Attributes:
        body: the main module of the model.
        head: preprocessing module before main module.
        tail: postprocessing module after main module.

    """

    def __init__(
        self,
        body,
        head=torch.nn.Identity(),
        tail=torch.nn.Identity(),
        hf: bool = False,
        input_names: Optional[List[str]] = None,
        concrete_args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.body = substitute_transform(
            body, hf=hf, input_names=input_names, concrete_args=concrete_args
        )
        self.head = head
        self.tail = tail

    def forward(self, input):
        r"""Runs a forward pass of the model on input and returns the output

        Args:
            input: A tensor or iterable that will be passed to the model

        Returns:
            output of a forward pass

        """
        output = self.head(input)
        if isinstance(output, torch.Tensor):
            output = (output,)
        output = [out for out in output if out is not None]
        output = self.body(*output)
        output = self.tail(output)
        return output

    def transform(self, config: Optional[Union[dict, str]], *rules):
        r"""
        Transform with Dmx-specific numerics/sparsity/logics

        NOTE: only staticly declared DmxModule(s) are to be transformed

        Args:
            config (Optional[Union[DmxConfig, str]]): DmxConfig to be used for transformation. 
            *rules (List[DmxConfigRule]): variable length of list of configuration rules on top of config.

        Returns:
            Returns the transformed model

        """
        if config is not None:
            if isinstance(config, str):
                config = DmxConfig.from_yaml(config)

            for n, m in self.named_dmx_modules():
                if n in config:
                    m.transform(config[n])

        for _r in rules:
            _r.apply_to(self)

        return self

    @property
    def dmx_config(self):
        r""" "Returns the DmxConfig object for the model"""
        return DmxConfig.from_model(self, freeze=True)

    @property
    def dmx_module_names(self):
        r""" "Returns a list of module names listed in a dmx_config"""
        return self.dmx_config.module_names

    def named_dmx_modules(self):
        r""" "Returns a list of named modules that are dmx configurable"""
        return ((n, m) for n, m in self.body.named_modules() if is_configurable(m))

    def freeze(self, config_file="./config.yaml"):
        """
        A function that stores the state and ops format of the model to a config file

        Args:
            config_file (Optional[str]): Path to store the config file. Defaults to "./config.yaml".
        """
        DmxConfig.from_model(self, freeze=True).to_yaml(config_file)

    def thaw(self, config_file="./config.yaml"):
        """
        A function that transforms the model in place from a config file.

        Args:
            config_file (Optional[str]): Path of config file to transform the model from. Defaults to "./config.yaml".
        """
        self.transform(config_file)

    def print_model_tree(self, include_type=False):
        """
        A function that prints out the tree structure of a model

        Args:
            include_type (bool): include the type of modules in the print out if True
        """
        from mltools.utils import print_model_tree

        print_model_tree(self, include_type)

    def fold_weights_and_biases(self):
        """
        A function that applies the ops the weights and biases using the corresponding formats.
        """
        for _, m in self.named_dmx_modules():
            m.fold_weight_and_bias()

    def check_dim_consistency(self) -> bool:
        """
        A function that checks format dimension consistency and sparseness dimension consistency for all applicable dmx modules in the model

        Returns:
            True if all dimensions are consistent for all applicable modules.
        """
        return all(
            m.check_format_dim_consistency() and m.check_sparseness_dim_consistency()
            for _, m in self.named_dmx_modules()
        )

    @contextmanager
    def keep_dmx_config(self) -> None:
        _dmx_config = self.dmx_config
        yield self
        self.transform(_dmx_config)

    @contextmanager
    def counting_flops(self, zero: bool = True) -> None:
        with ExitStack() as stack:
            yield [
                stack.enter_context(m.counting_flops(zero))
                for _, m in self.named_dmx_modules()
            ]

    @staticmethod
    def _save_specific_layers_state_dict_and_register_urls(
        specific_layers: Dict[str, Sequence[DmxModule]],
        save_checkpoint_to: Optional[str],
    ):
        if save_checkpoint_to is not None:
            for _, m in specific_layers:
                m.save_state_dict_and_register_url(parent_dir=save_checkpoint_to)

    @contextmanager
    def calibrating_weights(
        self,
        specific_layers: Optional[Dict[str, Sequence[DmxModule]]] = None,
        save_checkpoint_to: Optional[str] = None,
    ) -> None:
        if specific_layers is None:
            specific_layers = self.named_dmx_modules()
        with ExitStack() as stack:
            yield [
                stack.enter_context(m.calibrating_weight()) for _, m in specific_layers
            ]
        self._save_specific_layers_state_dict_and_register_urls(
            specific_layers, save_checkpoint_to
        )

    @contextmanager
    def calibrating_activations(
        self,
        specific_layers: Optional[Dict[str, Sequence[DmxModule]]] = None,
        save_checkpoint_to: Optional[str] = None,
    ) -> None:
        if specific_layers is None:
            specific_layers = self.named_dmx_modules()
        with ExitStack() as stack:
            yield [
                stack.enter_context(m.calibrating_activation())
                for _, m in specific_layers
            ]
        self._save_specific_layers_state_dict_and_register_urls(
            specific_layers, save_checkpoint_to
        )

    @contextmanager
    def calibrating_smoothquant(
        self,
        specific_layers: Optional[Dict[str, Sequence[DmxModule]]] = None,
        save_checkpoint_to: Optional[str] = None,
    ) -> None:
        if specific_layers is None:
            specific_layers = self.named_dmx_modules()
        with ExitStack() as stack:
            yield [
                stack.enter_context(m.calibrating_smoothquant())
                for _, m in specific_layers
            ]
        self._save_specific_layers_state_dict_and_register_urls(
            specific_layers, save_checkpoint_to
        )

    @contextmanager
    def optimal_brain_compressing(
        self,
        specific_layers: Optional[Dict[str, Sequence[DmxModule]]] = None,
        save_checkpoint_to: Optional[str] = None,
        **hyperparams,
    ) -> None:
        if specific_layers is None:
            specific_layers = self.named_dmx_modules()
        with ExitStack() as stack:
            yield [
                stack.enter_context(m.optimal_brain_compressing(**hyperparams))
                for _, m in specific_layers
            ]
        self._save_specific_layers_state_dict_and_register_urls(
            specific_layers, save_checkpoint_to
        )


class DmxConfig(dict):
    r"""
    This is a dict of Dmx-specific configurations for a dmx.Model
    This defines the 'states' to be optimized
    """

    @classmethod
    def from_model(cls, model: Model, freeze=False):
        """
        A function that stores state and ops format of the model in a DmxConfig object

        Args:
            model (Model): Target model for creating the DmxConfig

        Returns:
            A DmxConfig object that stores state and ops format of the model in a DmxConfig object
        """
        return cls({n: m.dmx_config(freeze) for n, m in model.named_dmx_modules()})

    @classmethod
    def from_yaml(cls, fname):
        """
        A function that creates a DmxConfig object from a yaml file

        Args:
            fname (str): file path of the yaml file

        Returns:
            A DmxConfig object vreated from yaml file
        """
        from mltools.utils import load_config_file

        return cls(load_config_file(fname))

    def to_yaml(self, fname):
        """
        A function that stores the content of a DmxConfig object to a yaml file

        Args:
            fname (str): file path of the yaml file
        """
        from mltools.utils import save_config_file

        save_config_file(dict(self), fname)

    @property
    def module_names(self):
        """
        Returns the module names in the DmxConfig object
        """
        return self.keys()


class DmxConfigRule(SimpleNamespace):
    r"""
    This is a rule that specifies how to transform from DmxConfig to DmxConfig
    This defines the 'action' in the state space

    Args:
        module_types (Optional[Tuple]): Types of modules to apply transformation. Defaults to empty Tuple.
        name_re (Optional[str]): String for matching module name patterns. Defaults to empty str.
        module_config (Optional[DmxModuleConfig]): DmxModuleConfig that specifies the ops formats for a module. Defaults to empty DmxModuleConfig.

    Attributes:
        module_types (Tuple): Types of modules to apply transformation.
        name_rule (Pattern): Pattern for matching module names.
        module_config (DmxModuleConfig): DmxModuleConfig that specifies the ops formats for a module.
    """

    def __init__(
        self,
        module_types=(),
        name_re: str = "",
        module_config: DmxModuleConfig = DmxModuleConfig(),
    ) -> None:
        assert all([issubclass(mt, DmxModule) for mt in module_types])
        self.module_types = module_types
        self.name_rule = re.compile(name_re)
        self.module_config = module_config

    def names_in(self, model_or_config: Union[Model, DmxConfig]):
        """
        Creates a list of module names where the modules are in self.module_types and the names match with self.name_rule.

        Args:
            model_or_config (Union[Model, DmxConfig]): Model or DmxConfig to create the name of modules for.

        Returns:
            A list of module names
        """
        config = (
            model_or_config
            if isinstance(model_or_config, DmxConfig)
            else DmxConfig.from_model(model_or_config, freeze=True)
        )
        return [
            n
            for n in config.module_names
            if config[n]["instance"] in self.module_types and self.name_rule.match(n)
        ]

    def apply_to(self, model_or_config: Union[Model, DmxConfig]):
        """
        A function that sets format of ops according to self.module_config for modules selected by self.module_types and
        self.name_rule on a model or DmxConfig

        Args:
            model_or_config (Union[Model, DmxConfig]): Model or DmxConfig to apply transformation on.
        """
        target_module_names = self.names_in(model_or_config)
        if isinstance(model_or_config, Model):
            for n, m in model_or_config.named_dmx_modules():
                if n in target_module_names and type(m) in self.module_types:
                    m.transform(self.module_config)
        else:
            config = model_or_config
            for n in (
                target_module_names
                and getattr(dmx.nn, config[n]["instance"]) in self.module_types
            ):
                config[n].update(self.module_config)


# alias for backward compatibility, to be deprecated
DmxTransformation = DmxConfigRule
