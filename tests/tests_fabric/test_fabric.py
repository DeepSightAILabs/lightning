# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
from re import escape
from unittest import mock
from unittest.mock import ANY, MagicMock, Mock, PropertyMock

import pytest
import torch
import torch.distributed
import torch.nn.functional
from lightning_utilities.test.warning import no_warning_call
from tests_fabric.helpers.runif import RunIf
from torch import nn
from torch.utils.data import DataLoader, DistributedSampler, RandomSampler, Sampler, SequentialSampler, TensorDataset

from lightning_fabric.fabric import Fabric
from lightning_fabric.plugins import Precision
from lightning_fabric.strategies import (
    DDPShardedStrategy,
    DDPStrategy,
    DeepSpeedStrategy,
    ParallelStrategy,
    SingleDeviceStrategy,
    Strategy,
    XLAStrategy,
)
from lightning_fabric.strategies.strategy import _Sharded
from lightning_fabric.utilities import _StrategyType
from lightning_fabric.utilities.exceptions import MisconfigurationException
from lightning_fabric.utilities.seed import pl_worker_init_function, seed_everything
from lightning_fabric.utilities.warnings import PossibleUserWarning
from lightning_fabric.wrappers import _FabricDataLoader, _FabricModule, _FabricOptimizer


class EmptyLite(Fabric):
    def run(self):
        pass


class BoringModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.layer = torch.nn.Linear(32, 2, bias=False)

    def forward(self, x):
        x = self.layer(x)
        return torch.nn.functional.mse_loss(x, torch.ones_like(x))


def test_run_input_output():
    """Test that the dynamically patched run() method receives the input arguments and returns the result."""

    class Lite(Fabric):

        run_args = ()
        run_kwargs = {}

        def run(self, *args, **kwargs):
            self.run_args = args
            self.run_kwargs = kwargs
            return "result"

    lite = Lite()
    result = lite.run(1, 2, three=3)
    assert result == "result"
    assert lite.run_args == (1, 2)
    assert lite.run_kwargs == {"three": 3}


@mock.patch("lightning_fabric.strategies.ddp.DistributedDataParallel")
@pytest.mark.parametrize("setup_method", ["setup", "setup_module"])
def test_setup_module(ddp_mock, setup_method):
    """Test that the setup method lets the strategy wrap the model, but keeps a reference to the original model."""
    lite = EmptyLite(accelerator="cpu", strategy="ddp", devices=2)
    model = nn.Linear(1, 2)
    setup_method = getattr(lite, setup_method)
    lite_model = setup_method(model)
    ddp_mock.assert_called_with(module=model, device_ids=ANY)
    assert lite_model.module == model
    assert lite_model.weight is model.weight
    assert lite_model.forward != model.forward


@pytest.mark.parametrize(
    "accelerator, initial_device, target_device",
    [
        ("cpu", "cpu", "cpu"),
        pytest.param("cpu", "cuda:0", "cpu", marks=RunIf(min_cuda_gpus=1)),
        pytest.param("cpu", "mps:0", "cpu", marks=RunIf(mps=True)),
        pytest.param("cuda", "cpu", "cuda:0", marks=RunIf(min_cuda_gpus=1)),
        pytest.param("cuda", "cuda:1", "cuda:0", marks=RunIf(min_cuda_gpus=2)),
        pytest.param("mps", "cpu", "mps:0", marks=RunIf(mps=True)),
    ],
)
@pytest.mark.parametrize("move_to_device", [True, False])
@pytest.mark.parametrize("setup_method", ["setup", "setup_module"])
def test_setup_module_move_to_device(setup_method, move_to_device, accelerator, initial_device, target_device):
    """Test that `move_to_device` leads to parameters being moved to the correct device and that the device
    attributes on the wrapper are updated."""
    initial_device = torch.device(initial_device)
    target_device = torch.device(target_device)
    expected_device = target_device if move_to_device else initial_device

    lite = EmptyLite(accelerator=accelerator, devices=1)
    model = nn.Linear(1, 2)
    model.to(initial_device)
    setup_method = getattr(lite, setup_method)
    lite_model = setup_method(model, move_to_device=move_to_device)

    # all parameters on the expected device
    assert all(param.device == expected_device for param in model.parameters())
    assert all(param.device == expected_device for param in lite_model.parameters())

    assert lite_model.device == expected_device
    assert lite.device == target_device


@RunIf(min_cuda_gpus=1)
@pytest.mark.parametrize("move_to_device", [True, False])
@pytest.mark.parametrize("setup_method", ["setup", "setup_module"])
def test_setup_module_parameters_on_different_devices(setup_method, move_to_device):
    """Test that a warning is emitted when model parameters are on a different device prior to calling
    `setup()`."""
    device0 = torch.device("cpu")
    device1 = torch.device("cuda", 0)

    lite = EmptyLite(accelerator="cuda", devices=1)

    module0 = nn.Linear(1, 2).to(device0)
    module1 = nn.Linear(1, 2).to(device1)
    model = nn.Sequential(module0, module1)

    setup_method = getattr(lite, setup_method)

    if move_to_device:
        with pytest.warns(PossibleUserWarning, match="has parameters on different devices"):
            lite_model = setup_method(model, move_to_device=move_to_device)

        # both have the same device now
        assert lite_model.device == device1
        assert module0.weight.device == module0.bias.device == device1
        assert module1.weight.device == module1.bias.device == device1
    else:
        with no_warning_call(expected_warning=PossibleUserWarning, match="has parameters on different devices"):
            setup_method(model, move_to_device=move_to_device)


def test_setup_module_and_optimizers():
    """Test that `setup()` can handle no optimizers, one optimizer, or multiple optimizers."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer0 = torch.optim.SGD(model.parameters(), lr=0.1)
    optimizer1 = torch.optim.Adam(model.parameters(), lr=0.1)

    # no optimizer
    lite_model = lite.setup(model)
    assert isinstance(lite_model, _FabricModule)
    assert lite_model.module is model

    # single optimizer
    lite_model, lite_optimizer = lite.setup(model, optimizer0)
    assert isinstance(lite_model, _FabricModule)
    assert isinstance(lite_optimizer, _FabricOptimizer)
    assert lite_model.module is model
    assert lite_optimizer.optimizer is optimizer0

    # multiple optimizers
    lite_model, lite_optimizer0, lite_optimizer1 = lite.setup(model, optimizer0, optimizer1)
    assert isinstance(lite_model, _FabricModule)
    assert isinstance(lite_optimizer0, _FabricOptimizer)
    assert isinstance(lite_optimizer1, _FabricOptimizer)
    assert lite_model.module is model
    assert lite_optimizer0.optimizer is optimizer0
    assert lite_optimizer1.optimizer is optimizer1


def test_setup_optimizers():
    """Test that `setup_optimizers()` can handle one or more optimizers."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer0 = torch.optim.SGD(model.parameters(), lr=0.1)
    optimizer1 = torch.optim.Adam(model.parameters(), lr=0.1)

    # single optimizer
    lite_optimizer = lite.setup_optimizers(optimizer0)
    assert isinstance(lite_optimizer, _FabricOptimizer)
    assert lite_optimizer.optimizer is optimizer0

    # multiple optimizers
    lite_optimizer0, lite_optimizer1 = lite.setup_optimizers(optimizer0, optimizer1)
    assert isinstance(lite_optimizer0, _FabricOptimizer)
    assert isinstance(lite_optimizer1, _FabricOptimizer)
    assert lite_optimizer0.optimizer is optimizer0
    assert lite_optimizer1.optimizer is optimizer1


def test_setup_twice_fails():
    """Test that calling `setup` with a model or optimizer that is already wrapped fails."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer = torch.optim.Adam(model.parameters())

    lite_model, lite_optimizer = lite.setup(model, optimizer)
    with pytest.raises(ValueError, match="A model should be passed only once to the"):
        lite.setup(lite_model, optimizer)

    lite_model, lite_optimizer = lite.setup(model, optimizer)
    with pytest.raises(ValueError, match="An optimizer should be passed only once to the"):
        lite.setup(model, lite_optimizer)


def test_setup_module_twice_fails():
    """Test that calling `setup_module` with a model that is already wrapped fails."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)

    lite_model = lite.setup_module(model)
    with pytest.raises(ValueError, match="A model should be passed only once to the"):
        lite.setup_module(lite_model)


def test_setup_optimizers_twice_fails():
    """Test that calling `setup_module` with a model that is already wrapped fails."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer = torch.optim.Adam(model.parameters())

    lite_optimizer = lite.setup_optimizers(optimizer)
    with pytest.raises(ValueError, match="An optimizer should be passed only once to"):
        lite.setup_optimizers(lite_optimizer)


def test_setup_module_not_supported():
    """Test that `setup_module` validates the strategy supports setting up model and optimizers independently."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    lite._strategy = Mock(spec=DDPShardedStrategy)
    with pytest.raises(RuntimeError, match=escape("requires the model and optimizer(s) to be set up jointly through")):
        lite.setup_module(model)


@pytest.mark.parametrize("strategy_cls", [DeepSpeedStrategy, DDPShardedStrategy, XLAStrategy])
def test_setup_optimizers_not_supported(strategy_cls):
    """Test that `setup_optimizers` validates the strategy supports setting up model and optimizers
    independently."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer = torch.optim.Adam(model.parameters())
    lite._strategy = Mock(spec=strategy_cls)
    with pytest.raises(RuntimeError, match=escape("requires the model and optimizer(s) to be set up jointly through")):
        lite.setup_optimizers(optimizer)


def test_setup_tracks_num_models():
    """Test that setup() tracks how many times it has setup a model."""
    lite = EmptyLite()
    model = nn.Linear(1, 2)
    optimizer = torch.optim.Adam(model.parameters())

    assert lite._models_setup == 0
    lite.setup(model, optimizer)
    assert lite._models_setup == 1

    lite.setup(model, optimizer)
    assert lite._models_setup == 2

    lite.setup_module(model)
    assert lite._models_setup == 3


def test_setup_dataloaders_unsupported_input():
    """Test that the setup_dataloaders method fails when provided with non-DataLoader objects."""
    lite = EmptyLite()
    with pytest.raises(ValueError, match="`setup_dataloaders` requires at least one dataloader"):
        lite.setup_dataloaders()
    with pytest.raises(TypeError, match="Only PyTorch DataLoader are currently supported"):
        lite.setup_dataloaders(range(2))  # type: ignore


def test_setup_dataloaders_return_type():
    """Test that the setup method returns the dataloaders wrapped as FabricDataLoader and in the right order."""
    lite = EmptyLite()

    # single dataloader
    lite_dataloader = lite.setup_dataloaders(DataLoader(range(2)))
    assert isinstance(lite_dataloader, _FabricDataLoader)

    # multiple dataloaders
    dataset0 = Mock()
    dataset1 = Mock()
    dataloader0 = DataLoader(dataset0)
    dataloader1 = DataLoader(dataset1)
    lite_dataloader0, lite_dataloader1 = lite.setup_dataloaders(dataloader0, dataloader1)
    assert isinstance(lite_dataloader0, _FabricDataLoader)
    assert isinstance(lite_dataloader1, _FabricDataLoader)
    assert lite_dataloader0.dataset is dataset0
    assert lite_dataloader1.dataset is dataset1


@mock.patch("lightning_fabric.fabric._replace_dunder_methods")
def test_setup_dataloaders_captures_dataloader_arguments(ctx_manager):
    """Test that Lite intercepts the DataLoader constructor arguments with a context manager in its run method."""

    class Lite(Fabric):
        def run(self):
            # One for BatchSampler, another for DataLoader
            assert ctx_manager().__enter__.call_count == 2

    Lite().run()
    assert ctx_manager().__exit__.call_count == 2


def test_setup_dataloaders_raises_for_unknown_custom_args():
    """Test that an error raises when custom dataloaders with unknown arguments are created from outside Lite's run
    method."""
    lite = EmptyLite()

    class CustomDataLoader(DataLoader):
        def __init__(self, new_arg, *args, **kwargs):
            super().__init__(range(5), *args, **kwargs)

    with pytest.raises(
        MisconfigurationException,
        match=(
            r"Trying to inject custom `Sampler` into the `CustomDataLoader` instance.*"
            r"The missing attributes are \['new_arg'\]"
        ),
    ):
        # The dataloader was not created within the run function, and therefore init args were not intercepted
        dataloader = CustomDataLoader(2, batch_size=2)
        lite.setup_dataloaders(dataloader)


def test_setup_dataloaders_twice_fails():
    """Test that calling setup_dataloaders with a dataloader that is already wrapped fails."""
    lite = EmptyLite()
    dataloader = DataLoader(range(2))
    lite_dataloader = lite.setup_dataloaders(dataloader)

    with pytest.raises(ValueError, match="A dataloader should be passed only once to the"):
        lite.setup_dataloaders(lite_dataloader)


@mock.patch(
    "lightning_fabric.fabric.Fabric.device",
    new_callable=PropertyMock,
    return_value=torch.device("cuda", 1),
)
def test_setup_dataloaders_move_to_device(lite_device_mock):
    """Test that the setup configures FabricDataLoader to move the data to the device automatically."""
    lite = EmptyLite()
    lite_dataloaders = lite.setup_dataloaders(DataLoader(Mock()), DataLoader(Mock()), move_to_device=False)
    assert all(dl.device is None for dl in lite_dataloaders)
    lite_device_mock.assert_not_called()

    lite = EmptyLite()
    lite_dataloaders = lite.setup_dataloaders(DataLoader(Mock()), DataLoader(Mock()), move_to_device=True)
    assert all(dl.device == torch.device("cuda", 1) for dl in lite_dataloaders)
    lite_device_mock.assert_called()


def test_setup_dataloaders_distributed_sampler_not_needed():
    """Test that replace_sampler option has no effect when no distributed sampler is needed."""
    custom_sampler = Mock(spec=Sampler)
    dataloader = DataLoader(Mock(), sampler=custom_sampler)

    # keep the custom sampler when not needed to replace
    lite = EmptyLite()
    lite_dataloader = lite.setup_dataloaders(dataloader, replace_sampler=True)
    assert lite_dataloader.sampler is custom_sampler


def test_setup_dataloaders_distributed_sampler_shuffle():
    """Test that the DataLoader(shuffle=True|False) setting gets carried over correctly into the distributed
    sampler."""
    lite = Fabric(accelerator="cpu", strategy="ddp_spawn", devices=2)
    # no lite.launch(): pretend we are on rank 0 now

    dataset = TensorDataset(torch.arange(8))

    # shuffling turned off
    no_shuffle_dataloaders = [
        DataLoader(dataset),
        DataLoader(dataset, shuffle=False),
        DataLoader(dataset, sampler=SequentialSampler(dataset)),
    ]
    for dataloader in no_shuffle_dataloaders:
        dataloader = lite.setup_dataloaders(dataloader)
        assert list(t[0].item() for t in iter(dataloader)) == [0, 2, 4, 6]

    # shuffling turned on
    shuffle_dataloaders = [DataLoader(dataset, shuffle=True), DataLoader(dataset, sampler=RandomSampler(dataset))]
    for dataloader in shuffle_dataloaders:
        seed_everything(1)
        dataloader = lite.setup_dataloaders(dataloader)
        assert list(t[0].item() for t in iter(dataloader)) == [5, 2, 7, 1]


@pytest.mark.parametrize("shuffle", [True, False])
@pytest.mark.parametrize("batch_size", [1, 2, 3])
def test_setup_dataloaders_distributed_sampler_parity(shuffle, batch_size):
    """Test that the distributed sampler setup in Lite leads to the same sequence of data as in raw PyTorch."""
    torch.manual_seed(1)
    lite = Fabric(accelerator="cpu", strategy="ddp", devices=2)
    # no lite.launch(): pretend we are on rank 0 now

    dataset = torch.arange(10)
    torch_dataloader = DataLoader(
        dataset,
        sampler=DistributedSampler(dataset, num_replicas=2, rank=0, shuffle=shuffle),
        batch_size=batch_size,
    )
    lite_dataloader = DataLoader(dataset, shuffle=shuffle, batch_size=batch_size)
    lite_dataloader = lite.setup_dataloaders(lite_dataloader)

    def fetch_epoch(loader):
        iterator = iter(loader)
        # we fetch 2 batches per epoch
        return torch.cat((next(iterator), next(iterator)))

    # 1st epoch
    # PyTorch users needs to set the epoch, while in Lite it gets handled automatically
    torch_dataloader.sampler.set_epoch(0)
    torch_data = fetch_epoch(torch_dataloader)
    lite_data = fetch_epoch(lite_dataloader)
    assert torch.equal(torch_data, lite_data)

    # 2nd epoch
    # PyTorch users needs to set the epoch, while in Lite it gets handled automatically
    torch_dataloader.sampler.set_epoch(1)
    torch_data = fetch_epoch(torch_dataloader)
    lite_data = fetch_epoch(lite_dataloader)
    assert torch.equal(torch_data, lite_data)
    assert torch_dataloader.sampler.epoch == 1
    assert lite_dataloader._dataloader.sampler.epoch == 1


@mock.patch.dict(os.environ, {}, clear=True)
def test_seed_everything():
    """Test that seed everything is static and sets the worker init function on the dataloader."""
    EmptyLite.seed_everything(3)

    lite = EmptyLite()
    lite_dataloader = lite.setup_dataloaders(DataLoader(Mock()))

    assert lite_dataloader.worker_init_fn.func is pl_worker_init_function
    assert os.environ == {"PL_GLOBAL_SEED": "3", "PL_SEED_WORKERS": "1"}


@pytest.mark.parametrize(
    "strategy",
    [
        _StrategyType.DP,
        _StrategyType.DDP,
        _StrategyType.DDP_SPAWN,
        pytest.param(_StrategyType.DDP_FORK, marks=RunIf(skip_windows=True)),
        pytest.param(_StrategyType.DEEPSPEED, marks=RunIf(deepspeed=True)),
        pytest.param(_StrategyType.DDP_SHARDED, marks=RunIf(fairscale=True)),
        pytest.param(_StrategyType.DDP_SHARDED_SPAWN, marks=RunIf(fairscale=True)),
    ],
)
def test_setup_dataloaders_replace_custom_sampler(strategy):
    """Test that asking to replace a custom sampler results in an error when a distributed sampler would be
    needed."""
    custom_sampler = Mock(spec=Sampler)
    dataloader = DataLoader(Mock(), sampler=custom_sampler)

    # explicitly asking to replace when a custom sampler is already configured raises an exception
    lite = EmptyLite(accelerator="cpu", strategy=strategy, devices=2)
    if lite._connector.is_distributed:
        with pytest.raises(TypeError, match="You seem to have configured a sampler in your DataLoader"):
            lite.setup_dataloaders(dataloader, replace_sampler=True)

    # setting `replace_sampler=False` leaves the sampler untouched
    lite_dataloader = lite.setup_dataloaders(dataloader, replace_sampler=False)
    assert lite_dataloader.sampler is custom_sampler


@pytest.mark.parametrize(
    "strategy",
    [
        _StrategyType.DP,
        _StrategyType.DDP,
        _StrategyType.DDP_SPAWN,
        pytest.param(_StrategyType.DDP_FORK, marks=RunIf(skip_windows=True)),
        pytest.param(_StrategyType.DEEPSPEED, marks=RunIf(deepspeed=True)),
        pytest.param(_StrategyType.DDP_SHARDED, marks=RunIf(fairscale=True)),
        pytest.param(_StrategyType.DDP_SHARDED_SPAWN, marks=RunIf(fairscale=True)),
    ],
)
@pytest.mark.parametrize("shuffle", [True, False])
def test_setup_dataloaders_replace_standard_sampler(shuffle, strategy):
    """Test that Lite replaces the default samplers with DistributedSampler automatically."""
    lite = EmptyLite(accelerator="cpu", strategy=strategy, devices=2)
    is_distributed = lite._connector.is_distributed
    lite_dataloader = lite.setup_dataloaders(DataLoader(range(3), shuffle=shuffle))
    assert not is_distributed or isinstance(lite_dataloader.sampler, DistributedSampler)


@pytest.mark.parametrize(
    "accelerator, expected",
    [
        ("cpu", "cpu"),
        pytest.param("cuda", "cuda:0", marks=RunIf(min_cuda_gpus=1)),
        pytest.param("gpu", "cuda:0", marks=RunIf(min_cuda_gpus=1)),
        pytest.param("tpu", "xla:0", marks=RunIf(tpu=True, standalone=True)),
        pytest.param("mps", "mps:0", marks=RunIf(mps=True)),
        pytest.param("gpu", "mps:0", marks=RunIf(mps=True)),
    ],
)
@mock.patch.dict(os.environ, os.environ.copy(), clear=True)
def test_to_device(accelerator, expected):
    """Test that the to_device method can move various objects to the device determined by the accelerator."""

    class Lite(Fabric):
        def run(self):
            expected_device = torch.device(expected)

            # module
            module = torch.nn.Linear(2, 3)
            module = lite.to_device(module)
            assert all(param.device == expected_device for param in module.parameters())

            # tensor
            tensor = torch.rand(2, 2)
            tensor = lite.to_device(tensor)
            assert tensor.device == expected_device

            # collection
            collection = {"data": torch.rand(2, 2), "int": 1}
            collection = lite.to_device(collection)
            assert collection["data"].device == expected_device

    lite = Lite(accelerator=accelerator, devices=1)
    lite.run()


def test_rank_properties():
    """Test that the rank properties are determined by the strategy."""
    lite = EmptyLite()
    lite._strategy = Mock(spec=Strategy)
    lite._strategy.world_size = 1000
    assert lite.world_size == 1000
    lite._strategy.global_rank = 100
    assert lite.global_rank == 100
    lite._strategy.local_rank = 10
    assert lite.local_rank == 10
    lite._strategy.node_rank = 1
    assert lite.node_rank == 1


def test_backward():
    """Test that backward() calls into the precision plugin."""
    lite = EmptyLite()
    lite._precision = Mock(spec=Precision)
    loss = Mock()
    lite.backward(loss, "arg", keyword="kwarg")
    lite._precision.backward.assert_called_with(loss, None, "arg", keyword="kwarg")


@RunIf(deepspeed=True)
def test_backward_model_input_required():
    """Test that when using deepspeed and multiple models, backward() requires the model as input."""
    lite = EmptyLite(strategy="deepspeed")

    model0 = nn.Linear(1, 2)
    model1 = nn.Linear(1, 2)

    optimizer0 = torch.optim.Adam(model0.parameters())
    optimizer1 = torch.optim.Adam(model1.parameters())

    lite._strategy.setup_module_and_optimizers = lambda *args: args

    lite.setup(model0, optimizer0)
    lite.setup(model1, optimizer1)

    loss = model0(torch.randn(1, 1)).sum()

    with pytest.raises(ValueError, match="please provide the model used to perform"):
        lite.backward(loss)


def test_autocast():
    """Test that the Lite autocast context manager lets the precision plugin handle casting."""
    lite = EmptyLite()
    lite._precision.forward_context = MagicMock()

    lite._precision.forward_context().__enter__.assert_not_called()
    with lite.autocast():
        lite._precision.forward_context().__enter__.assert_called()
    lite._precision.forward_context().__exit__.assert_called()


def test_no_backward_sync():
    """Test that `Lite.no_backward_sync()` validates the strategy and model is compatible."""
    lite = EmptyLite()
    model = nn.Linear(3, 3)
    with pytest.raises(TypeError, match="You need to set up the model first"):
        with lite.no_backward_sync(model):
            pass

    model = lite.setup(model)

    # pretend that the strategy does not support skipping backward sync
    lite._strategy = Mock(spec=ParallelStrategy, _backward_sync_control=None)
    with pytest.warns(PossibleUserWarning, match="The `ParallelStrategy` does not support skipping the"):
        with lite.no_backward_sync(model):
            pass

    # for single-device strategies, it becomes a no-op without warning
    lite._strategy = Mock(spec=SingleDeviceStrategy, _backward_sync_control=MagicMock())
    with lite.no_backward_sync(model):
        pass
    lite._strategy._backward_sync_control.no_backward_sync.assert_not_called()

    # pretend that the strategy supports skipping backward sync
    lite._strategy = Mock(_backward_sync_control=MagicMock())
    # disabling the context manager makes it a no-op
    with lite.no_backward_sync(model, enabled=False):
        pass
    lite._strategy._backward_sync_control.no_backward_sync.assert_not_called()
    # when enabld, the wrapped module gets passed down
    with lite.no_backward_sync(model):
        pass
    lite._strategy._backward_sync_control.no_backward_sync.assert_called_once_with(model._forward_module)


def test_launch_without_function():
    """Test the various ways `Fabric.launch()` can be called."""

    # default: no launcher, single process
    lite = Fabric()
    with mock.patch("lightning_fabric.fabric._do_nothing") as nothing:
        lite.launch()
    nothing.assert_called()

    # with a launcher on the strategy
    lite = Fabric()
    lite._strategy._launcher = Mock()
    lite.launch()
    lite._strategy._launcher.launch.assert_called()


def test_launch_with_function():
    """Test the various ways `Fabric.launch(function)` can be called."""

    def fn_without_args():
        pass

    lite = Fabric()
    with pytest.raises(TypeError, match="The function passed to .* needs to take at least one argument"):
        lite.launch(fn_without_args)

    def fn_with_one_arg(arg):
        assert isinstance(arg, Fabric)
        fn_with_one_arg.called = True

    lite = Fabric()
    lite.launch(fn_with_one_arg)
    assert fn_with_one_arg.called


@mock.patch.dict(os.environ, {"LT_CLI_USED": "1"})  # pretend we are using the CLI
def test_launch_and_cli_not_allowed():
    lite = Fabric()
    with pytest.raises(RuntimeError, match=escape("Calling  `.launch()` again is not allowed")):
        lite.launch()


@mock.patch.dict(os.environ, {"LT_CLI_USED": "1"})  # pretend we are using the CLI
def test_overridden_run_and_cli_not_allowed():
    class LiteWithRun(Fabric):
        def run(self):
            pass

    with pytest.raises(TypeError, match=escape("Overriding `Fabric.run()` and launching from the CLI is not allowed")):
        LiteWithRun()


def test_module_sharding_context():
    """Test that the sharding context manager gets applied when the strategy supports it and is a no-op
    otherwise."""
    lite = EmptyLite()
    lite._strategy = MagicMock(spec=DDPStrategy, module_sharded_context=Mock())
    with lite.sharded_model():
        pass
    lite._strategy.module_sharded_context.assert_not_called()

    lite._strategy = MagicMock(spec=_Sharded)
    with lite.sharded_model():
        pass
    lite._strategy.module_sharded_context.assert_called_once()


def test_callbacks_input():
    """Test the various ways in which callbacks can be registered with Fabric."""
    callback0 = Mock()
    callback1 = Mock()

    # single callback
    fabric = Fabric(callbacks=callback0)
    assert fabric._callbacks == [callback0]

    # multiple callbacks
    fabric = Fabric(callbacks=[callback0, callback1])
    assert fabric._callbacks == [callback0, callback1]


def test_call():
    """Test that `fabric.call` triggers the callback implementations."""
    callback0 = Mock()
    callback1 = Mock()
    fabric = Fabric(callbacks=[callback0, callback1])

    # No arguments
    fabric.call("on_train_end")
    callback0.on_train_end.assert_called_once()
    callback1.on_train_end.assert_called_once()

    # Optional arguments
    fabric.call("on_train_end", "positional", keyword="keyword")
    callback0.on_train_end.assert_called_with("positional", keyword="keyword")
    callback1.on_train_end.assert_called_with("positional", keyword="keyword")

    # Some callbacks don't implement the requested hook
    callback0 = Mock()
    callback1 = Mock(spec_set={})  # `on_train_end` not defined for this callback
    fabric = Fabric(callbacks=[callback0, callback1])
    fabric.call("on_train_end")
    callback0.on_train_end.assert_called_once()
    assert not callback1.mock_calls  # no methods were called on callback1

    # Skip callback attributes that are not callable
    callback = Mock(not_a_method=1)
    fabric = Fabric(callbacks=[callback])
    with pytest.warns(UserWarning, match="Skipping the callback `Mock.not_a_method`"):
        fabric.call("not_a_method")
    assert not callback1.mock_calls


def test_loggers_input():
    """Test the various ways in which loggers can be registered with Fabric."""
    logger0 = Mock()
    logger1 = Mock()

    # no logger
    fabric = Fabric(loggers=None)
    assert fabric._loggers == []
    fabric = Fabric(loggers=[])
    assert fabric._loggers == []

    # single logger
    fabric = Fabric(loggers=logger0)
    assert fabric._loggers == [logger0]

    # multiple loggers
    fabric = Fabric(loggers=[logger0, logger1])
    assert fabric._loggers == [logger0, logger1]


def test_log():
    """Test that `fabric.log` sends the metrics to each logger."""

    logger0 = Mock()
    logger1 = Mock()
    fabric = Fabric(loggers=[logger0, logger1])

    fabric.log("test", 1)
    logger0.log_metrics.assert_called_with(metrics={"test": 1}, step=None)
    logger1.log_metrics.assert_called_with(metrics={"test": 1}, step=None)

    fabric.log("test", 2, step=15)
    logger0.log_metrics.assert_called_with(metrics={"test": 2}, step=15)
    logger1.log_metrics.assert_called_with(metrics={"test": 2}, step=15)


def test_log_dict():
    """Test that `fabric.log_dict` sends the metrics dict to each logger."""

    logger0 = Mock()
    logger1 = Mock()
    fabric = Fabric(loggers=[logger0, logger1])

    fabric.log_dict({"foo": 1, "bar": 2}, step=None)
    logger0.log_metrics.assert_called_with(metrics={"foo": 1, "bar": 2}, step=None)
    logger1.log_metrics.assert_called_with(metrics={"foo": 1, "bar": 2}, step=None)

    fabric.log_dict({"foo": 3, "bar": 4}, step=15)
    logger0.log_metrics.assert_called_with(metrics={"foo": 3, "bar": 4}, step=15)
    logger1.log_metrics.assert_called_with(metrics={"foo": 3, "bar": 4}, step=15)
