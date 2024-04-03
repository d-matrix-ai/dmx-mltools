import torch
import functools
from inspect import signature
from typing import Union, Optional, get_origin, get_args
import transformers
from transformers import pipeline as hfpipeline
import evaluate
from datasets import load_dataset
from huggingface_hub import hf_hub_download
from mltools.fx.transform import substitute_transform
from .model import DmxModelMixin, DmxConfig

TASK_TO_INPUT_NAMES_LUT = {
    "text-generation": [
        "input_ids",
        "labels",
    ]  # is this correct?  text generation could need KV cache
}


def get_config_file(repo_name, revision, config_name):
    filename = f"configs/{config_name}.yaml"
    try:
        return hf_hub_download(repo_id=repo_name, filename=filename, revision=revision)
    except:
        return None


def dmx_transform(pipe, dmx_config_name):
    config_file = get_config_file(pipe.model_name, pipe.revision, dmx_config_name)
    if config_file is not None:
        config = DmxConfig.from_yaml(config_file)
        pipe.model.transform(config["model"])
    else:
        if dmx_config_name in ["BASELINE", "BASIC"]:
            from . import config_rules

            pipe.model.transform(
                pipe.baseline_config, *eval(f"config_rules.{dmx_config_name}")
            )
        else:
            raise RuntimeError(f"illegal dmx_config: {dmx_config_name}")


def eval_text_generation(
    model,
    dataset,
    metric,
    revision,
    column_name=None,
    dataset_version=None,
    dataset_split="test",
):
    dataset_column_mapping = {
        "wikitext": "text",
        "ptb_text_only": "sentence",
        "lambada": "text",
        "EleutherAI/lambada_openai": "text",
        # Add more datasets and their respective column names here
    }

    if not column_name and dataset in dataset_column_mapping:
        column_name = dataset_column_mapping[dataset]
    if not column_name:
        raise ValueError(
            f"Column name not found for dataset '{dataset}'. Please provide the column_name."
        )

    metric = evaluate.load(metric, module_type="metric")
    dataset = load_dataset(dataset, dataset_version, split=dataset_split)
    results = metric.compute(
        model=model, revision=revision, references=dataset[column_name]
    )
    return results


def pipe_eval(
    model,
    dataset,
    metric,
    revision,
    task,
    column_name=None,
    dataset_version=None,
    dataset_split="test",
):
    task_eval_mapping = {
        "text-generation": eval_text_generation,
        # Add more tasks here
    }

    if task not in task_eval_mapping:
        raise ValueError(f"Unsupported task type '{task}'.")

    eval_function = task_eval_mapping[task]
    return eval_function(
        model, dataset, metric, revision, column_name, dataset_version, dataset_split
    )


def pipeline(
    *args,
    dmx_config: Optional[str] = None,
    trust_remote_code: bool = True,
    device_map: Optional[str] = "auto",
    **kwargs,
):
    kwargs.update(
        {
            "trust_remote_code": trust_remote_code,
            "device_map": device_map,
        }
    )
    pipe = hfpipeline(*args, **kwargs)
    pipe.task = kwargs.get("task")
    pipe.model_name = kwargs.get("model")
    pipe.revision = kwargs.get("revision", "main")
    pipe.baseline_config = pipe.model.dmx_config
    pipe.evaluate = lambda metric, dataset, column_name=None, dataset_version=None, dataset_split="test": pipe_eval(
        pipe.model,
        dataset,
        metric,
        pipe.revision,
        pipe.task,
        column_name,
        dataset_version,
        dataset_split,
    )

    dmx_transform(pipe, dmx_config)

    return pipe


class SubstituteTransformedModule(torch.nn.Module):
    @classmethod
    def from_raw(cls, mod, input_names=None):
        _mod_signature = signature(mod.forward)
        _gm = substitute_transform(
            mod,
            hf=True,
            input_names=input_names,
        )
        _gm_signature = signature(_gm.forward)
        _gm._old_forward = _gm.forward

        def new_forward(_self, *args, **kwargs):
            _argument_dict = _mod_signature.bind(*args, **kwargs).arguments
            _argument_dict = {
                k: v
                for k, v in _argument_dict.items()
                if k in _gm_signature.parameters.keys()
            }
            _output = _self._old_forward(**_argument_dict)
            _output_cls = _mod_signature.return_annotation
            if get_origin(_output_cls) is Union:  # this is still error-prone
                _output_cls = get_args(_output_cls)[1]
                assert issubclass(_output_cls, transformers.modeling_utils.ModelOutput)
            return _output_cls(_output)

        if "GraphModuleImpl" in str(type(_gm)):
            _gm.__class__.forward = functools.update_wrapper(
                functools.partial(new_forward, _gm), _gm._old_forward
            )
        else:
            _gm.forward = functools.update_wrapper(
                functools.partial(new_forward, _gm), _gm._old_forward
            )
        mod.forward = _gm.forward
        return mod


def transform_children(_model):
    from mltools.utils import transform_submodule

    for _n, _ in _model.named_children():
        transform_submodule(
            _model,
            _n,
            lambda _m: SubstituteTransformedModule.from_raw(
                _m,
                input_names=["input_ids"]  # how do we not hard-code this?
                if _n == _model.base_model_prefix
                else None,
            ),
        )
    return _model


class DmxPreTrainedModel(transformers.modeling_utils.PreTrainedModel, DmxModelMixin):
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        _model = super().from_pretrained(*args, **kwargs)
        _model = transform_children(_model)
        return _model
