# ML tools

This project contains tools for deep neural net compression for d-Matrix hardware deployment.

  - [Overview](#overview)
  - [Getting started](#getting-started)
  - [API in a nutshell](#api-in-a-nutshell)
  - [Next steps](#next-steps)

---

## Overview

In essence this is an extension of the [PyTorch](https://pytorch.org/) framework that implements d-Matrix specific features, including 
- ***custom low-precision integer formats and arithmetic*** (for both activations and parameters), 
- ***fine-grain structured weight sparsity*** (for linear and convolutional weights), and 
- ***custom integer operation logics*** (for element-wise activation functions and normalizations).

It is so designed as 
- to provide a functionally complete set of d-Matrix specific features to augment native PyTorch ones in order to make them "Dmx-aware"
- to retain all framework functionalities in order to support Dmx-aware network compression and optimization.

### What is this project for?

- As a tool for algorithm research on Dmx-aware network compression and optimization
- As a tool for generation of ML workloads in production
- As an entry point for customer to optimize custom workloads for d-Matrix hardware deployment

### What is this project ***not*** for?

- HW emulation
- Performance modeling and analysis

## Getting started

The methods getting started with mltools are either
installing via pip your python environment, or using git to checkout the source
and then installing the libraries in pip's developer mode.
    
## API in a nutshell

Given a customer workload training/evaluation Python script, use the high-level API through two steps.

1. Import extended PyTorch that is Dmx-aware.  This is done by adding

    ```python
    dmx.aware()
    ```

    to the script.  This will augment `torch.nn` with Dmx-specific features, while retaining all PyTorch functionalities, _i.e._

    - all valid PyTorch model definitions remain valid and
    - all PyTorch models remain functionally equivalent, in both forward and backward passes, to those in native PyTorch.

2. Wrap a DNN in a `dmx.Model` container, _e.g._
   
    ```python
    model = dmx.Model(Net())
    ```

3. Define all Dmx-specific configurations in a `.yaml` file and transform a PyTorch `model` object by

    ```python
    model.transform("configs/dmx_example_config_lenet5.yaml")
    ```

    [This](configs/dmx_example_config_lenet5.yaml) is an example Dmx configuration file.  

The following code blocks show a simplest example usage.  

<table>
<tr>
<th>Original evaluation script</th>
<th>Modified evaluation script</th>
</tr>
<tr>
<td>

```python
import torch​
​
data = data_loader()​
model = some_network()​

​​
results = evaluate(model, data)​
```

</td>
<td>

```python
import torch​
from mltools import dmx
dmx.aware()

data = data_loader()​
model = some_network()​
model = dmx.Model(model)
model.transform('dmx_config.yaml') ​

results = evaluate(model, data)​
```

</td>
</tr>
</table>

## Next steps

For more detailed information, go over the following documents on specific topics.

### Dmx-aware features

- d-Matrix hardware execution
- [Numerics](docs/numerics.rst)
- Weight sparsity
- Custom logic
- Configurations for Dmx-specific transformation
- List of supported modules and models
- torch-mlir

### Examples

- Diffusers pipelines on LAION
- Transformer language models on SQuAD, Wikitext2, CNN_DailyMail
- Convolutional nets on CIFAR10/100 and Imagenet
- Multi-layer perceptron on MNIST

