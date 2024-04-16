from typing import Optional
from transformers import pipeline as hfpipeline
from mltools.fx.transform import substitute_transform
import transformers
from transformers import pipeline as hfpipeline
from .model import DmxModelMixin, DmxConfig
import evaluate
from datasets import load_dataset
from huggingface_hub import hf_hub_download
from .model import DmxModel, DmxConfig


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
        pipe.model.configure(config["model"])
    else:
        if dmx_config_name in ["BASELINE", "BASIC"]:
            from . import config_rules
            # NOTE: assuming pipe.model is in BASELINE mode
            pipe.model.configure(None, *eval(f"config_rules.{dmx_config_name}"))
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
    pipe.model = DmxModel.from_torch(
        pipe.model,
        concrete_args=None,
    )
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


class DmxPreTrainedModel(transformers.modeling_utils.PreTrainedModel, DmxModelMixin):
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        _model = super().from_pretrained(*args, **kwargs)
        _model.base_model = substitute_transform(_model.base_model, hf=True)
        return _model