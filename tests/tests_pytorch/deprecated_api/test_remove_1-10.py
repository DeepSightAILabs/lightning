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
"""Test deprecated functionality which will be removed in v1.10.0."""
from re import escape
from unittest import mock

import numpy
import pytest
import torch
from lightning_utilities.test.warning import no_warning_call
from torch.utils.data import DataLoader

import pytorch_lightning.profiler as profiler
from lightning_fabric.accelerators import CUDAAccelerator as LiteCUDAAccelerator
from lightning_fabric.accelerators import TPUAccelerator as LiteTPUAccelerator
from lightning_fabric.utilities.exceptions import MisconfigurationException
from pytorch_lightning import Trainer
from pytorch_lightning.accelerators.cpu import CPUAccelerator
from pytorch_lightning.cli import LightningCLI
from pytorch_lightning.core.mixins.device_dtype_mixin import DeviceDtypeModuleMixin
from pytorch_lightning.demos.boring_classes import BoringModel, RandomDataset
from pytorch_lightning.lite import LightningLite
from pytorch_lightning.overrides import LightningDistributedModule, LightningParallelModule
from pytorch_lightning.overrides.base import unwrap_lightning_module
from pytorch_lightning.overrides.fairscale import LightningShardedDataParallel, unwrap_lightning_module_sharded
from pytorch_lightning.plugins import ApexMixedPrecisionPlugin, DeepSpeedPrecisionPlugin, NativeMixedPrecisionPlugin
from pytorch_lightning.plugins.environments import LightningEnvironment
from pytorch_lightning.strategies.bagua import LightningBaguaModule
from pytorch_lightning.strategies.utils import on_colab_kaggle
from pytorch_lightning.trainer.states import RunningStage, TrainerFn
from pytorch_lightning.tuner.auto_gpu_select import pick_multiple_gpus, pick_single_gpu
from pytorch_lightning.utilities.apply_func import (
    apply_to_collection,
    apply_to_collections,
    convert_to_tensors,
    from_numpy,
    move_data_to_device,
    to_dtype_tensor,
    TransferableDataType,
)
from pytorch_lightning.utilities.cloud_io import atomic_save, get_filesystem, load
from pytorch_lightning.utilities.data import has_iterable_dataset, has_len
from pytorch_lightning.utilities.device_parser import (
    determine_root_gpu_device,
    is_cuda_available,
    num_cuda_devices,
    parse_cpu_cores,
    parse_gpu_ids,
    parse_hpus,
    parse_tpu_cores,
)
from pytorch_lightning.utilities.distributed import (
    all_gather_ddp_if_available,
    distributed_available,
    gather_all_tensors,
    get_default_process_group_backend_for_device,
    init_dist_connection,
    sync_ddp,
    sync_ddp_if_available,
    tpu_distributed,
)
from pytorch_lightning.utilities.enums import AMPType, PrecisionType
from pytorch_lightning.utilities.optimizer import optimizer_to_device, optimizers_to_device
from pytorch_lightning.utilities.seed import pl_worker_init_function, reset_seed, seed_everything
from pytorch_lightning.utilities.xla_device import inner_f, pl_multi_process, XLADeviceUtils
from tests_pytorch.helpers.runif import RunIf


@pytest.mark.parametrize(
    "wrapper_class",
    [
        LightningParallelModule,
        LightningDistributedModule,
        LightningBaguaModule,
        pytest.param(LightningShardedDataParallel, marks=RunIf(fairscale=True)),
    ],
)
def test_v1_10_deprecated_pl_module_init_parameter(wrapper_class):
    with no_warning_call(
        DeprecationWarning, match=rf"The argument `pl_module` in `{wrapper_class.__name__}` is deprecated in v1.8.0"
    ):
        wrapper_class(BoringModel())

    with pytest.deprecated_call(
        match=rf"The argument `pl_module` in `{wrapper_class.__name__}` is deprecated in v1.8.0"
    ):
        wrapper_class(pl_module=BoringModel())


def test_v1_10_deprecated_unwrap_lightning_module():
    with pytest.deprecated_call(match=r"The function `unwrap_lightning_module` is deprecated in v1.8.0"):
        unwrap_lightning_module(BoringModel())


@RunIf(fairscale=True)
def test_v1_10_deprecated_unwrap_lightning_module_sharded():
    with pytest.deprecated_call(match=r"The function `unwrap_lightning_module_sharded` is deprecated in v1.8.0"):
        unwrap_lightning_module_sharded(BoringModel())


def test_v1_10_deprecated_on_colab_kaggle_func():
    with pytest.deprecated_call(match="The function `on_colab_kaggle` has been deprecated in v1.8.0"):
        on_colab_kaggle()


def test_v1_10_deprecated_device_dtype_module_mixin():
    class MyModule(DeviceDtypeModuleMixin):
        pass

    with pytest.deprecated_call(match="mixins.DeviceDtypeModuleMixin` has been deprecated in v1.8.0"):
        MyModule()


def test_v1_10_deprecated_xla_device_utilities():
    with pytest.deprecated_call(match="xla_device.inner_f` has been deprecated in v1.8.0"):
        inner_f(mock.Mock(), mock.Mock())

    with pytest.deprecated_call(match="xla_device.pl_multi_process` has been deprecated in v1.8.0"):
        pl_multi_process(mock.Mock)

    with pytest.deprecated_call(match="xla_device.XLADeviceUtils` has been deprecated in v1.8.0"):
        XLADeviceUtils()

    with pytest.deprecated_call(match="xla_device.XLADeviceUtils.xla_available` has been deprecated in v1.8.0"):
        XLADeviceUtils.xla_available()

    with pytest.deprecated_call(match="xla_device.XLADeviceUtils.tpu_device_exists` has been deprecated in v1.8.0"):
        XLADeviceUtils.tpu_device_exists()

    from pytorch_lightning.utilities.distributed import tpu_distributed

    with pytest.deprecated_call(match="tpu_distributed` has been deprecated in v1.8.0"):
        tpu_distributed()


def test_v1_10_deprecated_apply_func_utilities():
    with pytest.deprecated_call(match="apply_func.apply_to_collection` has been deprecated in v1.8.0"):
        apply_to_collection([], dtype=object, function=(lambda x: x))

    with pytest.deprecated_call(match="apply_func.apply_to_collections` has been deprecated in v1.8.0"):
        apply_to_collections([], [], dtype=object, function=(lambda x, y: x))

    with pytest.deprecated_call(match="apply_func.convert_to_tensors` has been deprecated in v1.8.0"):
        convert_to_tensors([], torch.device("cpu"))

    with pytest.deprecated_call(match="apply_func.from_numpy` has been deprecated in v1.8.0"):
        from_numpy(numpy.zeros(2), torch.device("cpu"))

    with pytest.deprecated_call(match="apply_func.move_data_to_device` has been deprecated in v1.8.0"):
        move_data_to_device(torch.tensor(2), torch.device("cpu"))

    with pytest.deprecated_call(match="apply_func.to_dtype_tensor` has been deprecated in v1.8.0"):
        to_dtype_tensor(torch.tensor(2), dtype=torch.float32, device=torch.device("cpu"))

    class MyModule(TransferableDataType):
        pass

    with pytest.deprecated_call(match="apply_func.TransferableDataType` has been deprecated in v1.8.0"):
        MyModule()


def test_v1_10_deprecated_cloud_io_utilities(tmpdir):
    with pytest.deprecated_call(match="cloud_io.atomic_save` has been deprecated in v1.8.0"):
        atomic_save({}, tmpdir / "atomic_save.ckpt")

    with pytest.deprecated_call(match="cloud_io.get_filesystem` has been deprecated in v1.8.0"):
        get_filesystem(tmpdir)

    with pytest.deprecated_call(match="cloud_io.load` has been deprecated in v1.8.0"):
        load(str(tmpdir / "atomic_save.ckpt"))


def test_v1_10_deprecated_data_utilities():
    with pytest.deprecated_call(match="data.has_iterable_dataset` has been deprecated in v1.8.0"):
        has_iterable_dataset(DataLoader(RandomDataset(2, 4)))

    with pytest.deprecated_call(match="data.has_len` has been deprecated in v1.8.0"):
        has_len(DataLoader(RandomDataset(2, 4)))


def test_v1_10_deprecated_device_parser_utilities():
    with pytest.deprecated_call(match="device_parser.determine_root_gpu_device` has been deprecated in v1.8.0"):
        determine_root_gpu_device(None)

    with pytest.deprecated_call(match="device_parser.is_cuda_available` has been deprecated in v1.8.0"):
        is_cuda_available()

    with pytest.deprecated_call(match="device_parser.num_cuda_devices` has been deprecated in v1.8.0"):
        num_cuda_devices()

    with pytest.deprecated_call(match="device_parser.parse_hpus` has been deprecated in v1.8.0"):
        parse_hpus(1)

    with pytest.deprecated_call(match="device_parser.parse_cpu_cores` has been deprecated in v1.8.0"):
        parse_cpu_cores(1)

    with pytest.deprecated_call(match="device_parser.parse_gpu_ids` has been deprecated in v1.8.0"):
        parse_gpu_ids(None)

    with pytest.deprecated_call(match="device_parser.parse_tpu_cores` has been deprecated in v1.8.0"):
        parse_tpu_cores(None)


def test_v1_10_deprecated_distributed_utilities():
    with pytest.deprecated_call(match="distributed.all_gather_ddp_if_available` has been deprecated in v1.8.0"):
        all_gather_ddp_if_available(torch.tensor(1))

    with pytest.deprecated_call(match="distributed.distributed_available` has been deprecated in v1.8.0"):
        distributed_available()

    with mock.patch("torch.distributed.get_world_size", return_value=2), mock.patch(
        "torch.distributed.barrier"
    ), mock.patch("torch.distributed.all_gather"):
        with pytest.deprecated_call(match="distributed.gather_all_tensors` has been deprecated in v1.8.0"):
            gather_all_tensors(torch.tensor(1))

    with pytest.deprecated_call(
        match="distributed.get_default_process_group_backend_for_device` has been deprecated in v1.8.0"
    ):
        get_default_process_group_backend_for_device(torch.device("cpu"))

    with mock.patch("torch.distributed.is_initialized", return_value=True):
        with pytest.deprecated_call(match="distributed.init_dist_connection` has been deprecated in v1.8.0"):
            init_dist_connection(LightningEnvironment(), "gloo")

    with pytest.deprecated_call(match="distributed.sync_ddp_if_available` has been deprecated in v1.8.0"):
        sync_ddp_if_available(torch.tensor(1))

    with mock.patch("torch.distributed.barrier"), mock.patch("torch.distributed.all_reduce"):
        with pytest.deprecated_call(match="distributed.sync_ddp` has been deprecated in v1.8.0"):
            sync_ddp(torch.tensor(1))

    with pytest.deprecated_call(match="distributed.tpu_distributed` has been deprecated in v1.8.0"):
        tpu_distributed()


def test_v1_10_deprecated_optimizer_utilities():
    with pytest.deprecated_call(match="optimizer.optimizers_to_device` has been deprecated in v1.8.0"):
        optimizers_to_device([torch.optim.Adam(torch.nn.Linear(1, 1).parameters())], "cpu")

    with pytest.deprecated_call(match="optimizer.optimizer_to_device` has been deprecated in v1.8.0"):
        optimizer_to_device(torch.optim.Adam(torch.nn.Linear(1, 1).parameters()), "cpu")


def test_v1_10_deprecated_seed_utilities():
    with pytest.deprecated_call(match="seed.seed_everything` has been deprecated in v1.8.0"):
        seed_everything(1)

    with pytest.deprecated_call(match="seed.reset_seed` has been deprecated in v1.8.0"):
        reset_seed()

    with pytest.deprecated_call(match="seed.pl_worker_init_function` has been deprecated in v1.8.0"):
        pl_worker_init_function(0)


def test_v1_10_deprecated_accelerator_setup_environment_method():
    with pytest.deprecated_call(match="`Accelerator.setup_environment` has been deprecated in deprecated in v1.8.0"):
        CPUAccelerator().setup_environment(torch.device("cpu"))


class EmptyLite(LightningLite):
    def run(self):
        pass


def test_lite_convert_deprecated_gpus_argument(cuda_count_2):
    with pytest.deprecated_call(match=escape("Setting `Lite(gpus=2)` is deprecated in v1.8.0")):
        lite = EmptyLite(gpus=2)
    assert isinstance(lite._accelerator, LiteCUDAAccelerator)
    assert lite._connector._parallel_devices == [torch.device("cuda", 0), torch.device("cuda", 1)]


@RunIf(skip_windows=True)
def test_lite_convert_deprecated_tpus_argument(tpu_available):
    with pytest.deprecated_call(match=escape("Setting `Lite(tpu_cores=8)` is deprecated in v1.8.0")):
        lite = EmptyLite(tpu_cores=8)
    assert isinstance(lite._accelerator, LiteTPUAccelerator)
    assert lite._connector._parallel_devices == list(range(8))


@pytest.mark.parametrize(
    ["name", "value"],
    [("save_config_filename", "config.yaml"), ("save_config_overwrite", False), ("save_config_multifile", False)],
)
def test_lightningCLI_save_config_init_params_deprecation_warning(name, value):
    with mock.patch("sys.argv", ["any.py"]), pytest.deprecated_call(match=f".*{name!r} init parameter is deprecated.*"):
        LightningCLI(BoringModel, run=False, **{name: value})


def test_tuning_enum():
    with pytest.deprecated_call(
        match="`TrainerFn.TUNING` has been deprecated in v1.8.0 and will be removed in v1.10.0."
    ):
        TrainerFn.TUNING

    with pytest.deprecated_call(
        match="`RunningStage.TUNING` has been deprecated in v1.8.0 and will be removed in v1.10.0."
    ):
        RunningStage.TUNING


def test_tuning_trainer_property():
    trainer = Trainer()

    with pytest.deprecated_call(match="`Trainer.tuning` has been deprecated in v1.8.0 and will be removed in v1.10.0."):
        trainer.tuning

    with pytest.deprecated_call(
        match="Setting `Trainer.tuning` has been deprecated in v1.8.0 and will be removed in v1.10.0."
    ):
        trainer.tuning = True


@RunIf(skip_windows=True)
def test_v1_8_0_deprecated_all_gather_grad():
    tensor1 = torch.ones(1, requires_grad=True)
    with mock.patch("torch.distributed.all_gather"), mock.patch("torch.distributed.get_world_size", return_value=1):
        from pytorch_lightning.utilities import AllGatherGrad

        with pytest.deprecated_call(match="`AllGatherGrad` has been deprecated in v1.8"):
            AllGatherGrad.apply(tensor1)


def test_v1_8_1_deprecated_rank_zero_only():
    from pytorch_lightning.utilities.distributed import rank_zero_only

    with pytest.deprecated_call(match="rank_zero_only` has been deprecated in v1.8.1"):
        rank_zero_only(lambda: None)


@pytest.mark.parametrize(
    "cls",
    [
        profiler.AdvancedProfiler,
        profiler.PassThroughProfiler,
        profiler.PyTorchProfiler,
        profiler.SimpleProfiler,
        pytest.param(profiler.XLAProfiler, marks=RunIf(tpu=True)),
    ],
)
def test_profiler_classes_deprecated_warning(cls):
    with pytest.deprecated_call(
        match=f"profiler.{cls.__name__}` is deprecated in v1.9.0 and will be removed in v1.10.0."
        f" Use .*profilers.{cls.__name__}` class instead."
    ):
        cls()


@RunIf(amp_apex=True)
def test_apex_deprecation_warnings():
    class MyModel(BoringModel):
        def optimizer_step(
            self,
            epoch,
            batch_idx,
            optimizer,
            optimizer_idx=0,
            optimizer_closure=None,
            on_tpu=False,
            using_native_amp=False,
            **kwargs,
        ):
            return optimizer_closure()

    model = MyModel()
    trainer = Trainer(fast_dev_run=True)
    with pytest.deprecated_call(match="including the `using_native_amp` argument"):
        trainer.fit(model)

    with pytest.deprecated_call(match="ApexMixedPrecisionPlugin` class will be removed in v1.10"):
        ApexMixedPrecisionPlugin()

    with pytest.deprecated_call(match="NativeMixedPrecisionPlugin` class has been renamed in v1.9"):
        NativeMixedPrecisionPlugin(16, "cpu")

    with pytest.deprecated_call(match="Support for.*DeepSpeed implementation will be removed in v1.10.0"):
        DeepSpeedPrecisionPlugin(16, amp_type="apex")

    with pytest.deprecated_call(match=r"amp_type='native'\)` been deprecated in v1.9"):
        DeepSpeedPrecisionPlugin(16, amp_type="native")

    with pytest.raises(ValueError, match=r"amp_level='O2'\)` is only relevant when using NVIDIA/apex"):
        DeepSpeedPrecisionPlugin(16, amp_level="O2")

    with pytest.deprecated_call(match=r"Trainer\(amp_backend='apex'\)` argument is deprecated"):
        Trainer(amp_backend="apex")

    with pytest.deprecated_call(match=r"Trainer\(amp_level='O2'\)` argument is deprecated"):
        Trainer(amp_backend="apex", amp_level="O2")

    with pytest.deprecated_call(match="AMPType` enum has been deprecated in v1.9"):
        AMPType.APEX

    trainer = Trainer()
    with pytest.deprecated_call(match="amp_backend` will not be supported"):
        trainer.amp_backend


@RunIf(horovod=True)
def test_horovod_deprecation_warnings(*_):
    with pytest.deprecated_call(match=r"horovod'\)` has been deprecated in v1.9"):
        Trainer(strategy="horovod")


def test_auto_select_gpus():
    with pytest.deprecated_call(match="The Trainer argument `auto_select_gpus` has been deprecated in v1.9.0"):
        Trainer(auto_select_gpus=False)


def test_pick_multiple_gpus():
    with pytest.deprecated_call(match="The function `pick_multiple_gpus` has been deprecated in v1.9.0"), pytest.raises(
        MisconfigurationException
    ):
        pick_multiple_gpus(0)


@mock.patch("pytorch_lightning.tuner.auto_gpu_select.num_cuda_devices", return_value=0)
def test_pick_single_gpu(_):
    with pytest.deprecated_call(match="The function `pick_single_gpu` has been deprecated in v1.9.0"), pytest.raises(
        RuntimeError
    ):
        pick_single_gpu([])


def test_deprecated_precision_type():
    with pytest.deprecated_call(match="PrecisionType` enum has been deprecated in v1.9"):
        _ = PrecisionType.HALF
