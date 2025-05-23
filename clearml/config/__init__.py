""" Configuration module. Uses backend_config to load system configuration. """
import logging
import os
import sys
from os.path import expandvars, expanduser
from typing import Optional, Callable, Iterable, Tuple, List, Any
from pathlib2 import Path

from .defs import *  # noqa: F403
from .remote import running_remotely_task_id as _running_remotely_task_id
from ..backend_api import load_config
from ..backend_config import Config
from ..backend_config.bucket_config import S3BucketConfigurations

# config_obj = load_config(Path(__file__).parent)  # noqa: F405
# config_obj.initialize_logging()
# config = config_obj.get("sdk")
from ..utilities.proxy_object import LazyEvalWrapper


class ConfigWrapper(object):
    _config = None

    @classmethod
    def _init(cls) -> Config:
        if cls._config is None:
            cls._config = load_config(Path(__file__).parent)  # noqa: F405
            cls._config.initialize_logging()
        return cls._config

    @classmethod
    def get(cls, *args: Any, **kwargs: Any) -> Any:
        cls._init()
        return cls._config.get(*args, **kwargs)

    @classmethod
    def set_overrides(cls, *args: Any, **kwargs: Any) -> None:
        cls._init()
        return cls._config.set_overrides(*args, **kwargs)

    @classmethod
    def set_config_impl(cls, value: "ConfigWrapper") -> None:
        try:
            if issubclass(value, ConfigWrapper):
                cls._config = value._config
        except TypeError:
            cls._config = value


class ConfigSDKWrapper(object):
    _config_sdk = None

    @classmethod
    def _init(cls) -> None:
        if cls._config_sdk is None:
            cls._config_sdk = ConfigWrapper.get("sdk")

    @classmethod
    def get(cls, *args: Any, **kwargs: Any) -> Any:
        cls._init()
        return cls._config_sdk.get(*args, **kwargs)

    @classmethod
    def set_overrides(cls, *args: Any, **kwargs: Any) -> None:
        cls._init()
        return cls._config_sdk.set_overrides(*args, **kwargs)

    @classmethod
    def clear_config_impl(cls) -> None:
        cls._config_sdk = None


def deferred_config(
    key: Optional[str] = None,
    default: Any = Config.MISSING,
    transform: Optional[Callable[[], Any]] = None,
    multi: Optional[Iterable[Tuple[Any]]] = None,
) -> LazyEvalWrapper:
    return LazyEvalWrapper(
        callback=lambda: (
            ConfigSDKWrapper.get(key, default)
            if not multi
            else next(
                (ConfigSDKWrapper.get(*a) for a in multi if ConfigSDKWrapper.get(*a)),
                None,
            )
        )
        if transform is None
        else (
            transform()
            if key is None
            else transform(
                ConfigSDKWrapper.get(key, default)
                if not multi
                else next(
                    (ConfigSDKWrapper.get(*a) for a in multi if ConfigSDKWrapper.get(*a)),
                    None,
                )  # noqa
            )
        )
    )


config_obj = ConfigWrapper
config = ConfigSDKWrapper

""" Configuration object reflecting the merged SDK section of all available configuration files """


def get_cache_dir() -> Path:
    cache_base_dir = Path(  # noqa: F405
        expandvars(
            expanduser(
                CLEARML_CACHE_DIR.get()  # noqa: F405
                or config.get("storage.cache.default_base_dir")
                or DEFAULT_CACHE_DIR  # noqa: F405
            )
        )
    )
    return cache_base_dir


def get_offline_dir(task_id: str = None) -> Path:
    if not task_id:
        return get_cache_dir() / "offline"
    return get_cache_dir() / "offline" / task_id


def get_config_for_bucket(base_url: str, extra_configurations: Optional[List[Any]] = None) -> Any:
    config_list = S3BucketConfigurations.from_config(config.get("aws.s3"))

    for configuration in extra_configurations or []:
        config_list.add_config(configuration)

    return config_list.get_config_by_uri(base_url)


def get_remote_task_id() -> str:
    return _running_remotely_task_id


def running_remotely() -> bool:
    return bool(_running_remotely_task_id)


def get_log_to_backend(default: Optional[bool] = None) -> bool:
    return LOG_TO_BACKEND_ENV_VAR.get(default=default)  # noqa: F405


def get_node_count() -> Optional[int]:
    # noinspection PyBroadException
    try:
        mpi_world_rank = int(os.environ.get("OMPI_COMM_WORLD_NODE_RANK", os.environ.get("PMI_RANK")))
        return mpi_world_rank
    except Exception:
        pass

    # noinspection PyBroadException
    try:
        mpi_rank = int(os.environ.get("OMPI_COMM_WORLD_RANK", os.environ.get("SLURM_JOB_NUM_NODES")))
        return mpi_rank
    except Exception:
        pass

    # check if we have pyTorch node/worker ID (only if torch was already imported)
    if "torch" in sys.modules:
        # noinspection PyBroadException
        try:
            from torch.utils.data.dataloader import get_worker_info  # noqa

            worker_info = get_worker_info()
            if worker_info:
                return int(worker_info.num_workers)
        except Exception:
            pass

    return None


def get_node_id(default: int = 0) -> int:
    node_id = NODE_ID_ENV_VAR.get()  # noqa: F405

    # noinspection PyBroadException
    try:
        mpi_world_rank = int(os.environ.get("OMPI_COMM_WORLD_NODE_RANK", os.environ.get("PMI_RANK")))
    except Exception:
        mpi_world_rank = None

    # noinspection PyBroadException
    try:
        mpi_rank = int(
            os.environ.get(
                "OMPI_COMM_WORLD_RANK",
                os.environ.get("SLURM_PROCID", os.environ.get("SLURM_NODEID")),
            )
        )
    except Exception:
        mpi_rank = None

    # if we have no node_id, use the mpi rank
    if node_id is None and (mpi_world_rank is not None or mpi_rank is not None):
        node_id = mpi_world_rank if mpi_world_rank is not None else mpi_rank

    # if node is still None, use the global RANK
    if node_id is None:
        # noinspection PyBroadException
        try:
            node_id = int(os.environ.get("RANK"))
        except Exception:
            pass

    # if node is still None, use the default
    if node_id is None:
        node_id = default

    torch_rank = None
    # check if we have pyTorch node/worker ID (only if torch was already imported)
    if "torch" in sys.modules:
        # noinspection PyBroadException
        try:
            from torch.utils.data.dataloader import get_worker_info  # noqa

            worker_info = get_worker_info()
            if not worker_info:
                torch_rank = None
            else:
                w_id = worker_info.id
                # noinspection PyBroadException
                try:
                    torch_rank = int(w_id)
                except Exception:
                    # guess a number based on wid hopefully unique value
                    import hashlib

                    h = hashlib.md5()
                    h.update(str(w_id).encode("utf-8"))
                    torch_rank = int(h.hexdigest(), 16)
        except Exception:
            torch_rank = None

    # if we also have a torch rank add it to the node rank
    if torch_rank is not None:
        # Since we dont know the world rank, we assume it is not bigger than 10k
        node_id = (10000 * node_id) + torch_rank

    return node_id


def get_is_master_node() -> bool:
    global __force_master_node
    if __force_master_node:
        return True

    return get_node_id(default=0) == 0


def get_log_redirect_level() -> Optional[int]:
    """Returns which log level (and up) should be redirected to stderr. None means no redirection."""
    value = LOG_STDERR_REDIRECT_LEVEL.get()  # noqa: F405
    try:
        if value:
            return logging._checkLevel(value)  # noqa
    except (ValueError, TypeError, AttributeError):
        pass


def dev_worker_name() -> str:
    return DEV_WORKER_NAME.get()  # noqa: F405


def __set_is_master_node() -> Optional[bool]:
    # noinspection PyBroadException
    try:
        # pop both set the first
        env_a = os.environ.pop("CLEARML_FORCE_MASTER_NODE", None)
        env_b = os.environ.pop("TRAINS_FORCE_MASTER_NODE", None)
        force_master_node = env_a or env_b
    except Exception:
        force_master_node = None

    if force_master_node is not None:
        # noinspection PyBroadException
        try:
            force_master_node = bool(int(force_master_node))
        except Exception:
            force_master_node = None

    return force_master_node


__force_master_node = __set_is_master_node()
