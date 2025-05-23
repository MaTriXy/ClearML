import itertools
import json
from copy import copy
from logging import getLogger
from typing import Callable, Union, Optional, Mapping, Tuple, Dict, Any

import six
import yaml


class ProxyDictPostWrite(dict):
    """Dictionary wrapper that updates an arguments instance on any item set in the dictionary"""

    def __init__(
        self,
        update_obj: Any,
        update_func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super(ProxyDictPostWrite, self).__init__(*args, **kwargs)
        self._update_obj = update_obj
        self._update_func = None
        for k, i in self.items():
            if isinstance(i, dict):
                super(ProxyDictPostWrite, self).update({k: ProxyDictPostWrite(update_obj, self._set_callback, i)})
        self._update_func = update_func

    def __setitem__(self, key: Any, value: Any) -> None:
        super(ProxyDictPostWrite, self).__setitem__(key, value)
        self._set_callback()

    def __reduce__(self) -> tuple:
        return dict, (), None, None, iter(self._to_dict().items())

    def _set_callback(self, *_: Any) -> None:
        if self._update_func:
            self._update_func(self._update_obj, self)

    def _to_dict(self) -> dict:
        a_dict = {}
        for k, i in self.items():
            if isinstance(i, ProxyDictPostWrite):
                a_dict[k] = i._to_dict()
            else:
                a_dict[k] = i
        return a_dict

    def to_dict(self) -> dict:
        return self._to_dict()

    def update(self, E: Optional[Union[dict, Mapping]] = None, **F: Any) -> None:
        res = self._do_update(E, **F)
        self._set_callback()
        return res

    def _do_update(self, E: Optional[Union[dict, "ProxyDictPostWrite"]] = None, **F: Any) -> None:
        res = super(ProxyDictPostWrite, self).update(
            ProxyDictPostWrite(self._update_obj, self._set_callback, E)
            if E is not None
            else ProxyDictPostWrite(self._update_obj, self._set_callback, **F)
        )
        return res


class ProxyDictPreWrite(dict):
    """Dictionary wrapper that prevents modifications to the dictionary"""

    def __init__(
        self,
        update_obj: Any,
        update_func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super(ProxyDictPreWrite, self).__init__(*args, **kwargs)
        self._update_func = None
        for k, i in self.items():
            if isinstance(i, dict):
                self.update({k: ProxyDictPreWrite(k, self._nested_callback, i)})
        self._update_obj = update_obj
        self._update_func = update_func

    def __reduce__(self) -> tuple:
        return dict, (), None, None, iter(self.items())

    def __setitem__(self, key: Any, value: Any) -> None:
        key_value = self._set_callback(
            (
                key,
                value,
            )
        )
        if key_value:
            super(ProxyDictPreWrite, self).__setitem__(*key_value)

    def _set_callback(self, key_value: Any, *_: Any) -> Optional[Any]:
        if self._update_func is not None:
            if callable(self._update_func):
                res = self._update_func(self._update_obj, key_value)
            else:
                res = self._update_func

            if not res:
                return None
            return res
        return key_value

    def _nested_callback(self, prefix: str, key_value: tuple) -> tuple:
        return self._set_callback(
            (
                prefix + "." + key_value[0],
                key_value[1],
            )
        )


class StubObject(object):
    def __call__(self, *args: Any, **kwargs: Any) -> "StubObject":
        return self

    def __getattr__(self, attr: str) -> "StubObject":
        return self

    def __setattr__(self, attr: str, val: Any) -> None:
        pass


def verify_basic_type(
    a_dict_list: Union[float, int, bool, str, list, tuple, dict],
    basic_types: Optional[Tuple[Union[Any, Tuple[Any]]]] = None,
) -> bool:
    basic_types = (
        (
            float,
            int,
            bool,
            six.string_types,
        )
        if not basic_types
        else tuple(b for b in basic_types if b not in (list, tuple, dict))
    )

    if isinstance(a_dict_list, basic_types):
        return True
    if isinstance(a_dict_list, (list, tuple)):
        return all(verify_basic_type(v, basic_types=basic_types) for v in a_dict_list)
    elif isinstance(a_dict_list, dict):
        return all(verify_basic_type(k, basic_types=basic_types) for k in a_dict_list.keys()) and all(
            verify_basic_type(v, basic_types=basic_types) for v in a_dict_list.values()
        )


def convert_bool(s: str) -> bool:
    s = s.strip().lower()
    if s == "true":
        return True
    elif s == "false" or not s:
        return False
    raise ValueError("Invalid value (boolean literal expected): {}".format(s))


def cast_basic_type(value: Any, type_str: str) -> Any:
    if not type_str:
        # empty string with no type is treated as None
        if value == "":
            return None
        return value

    basic_types = {str(getattr(v, "__name__", v)): v for v in (float, int, str, list, tuple, dict)}
    basic_types["bool"] = convert_bool

    parts = type_str.split("/")
    # nested = len(parts) > 1

    if parts[0] in ("list", "tuple", "dict"):
        # noinspection PyBroadException
        try:
            # lists/tuple/dicts should be json loadable
            return basic_types.get(parts[0])(json.loads(value))
        except Exception:
            # noinspection PyBroadException
            try:
                # fallback to legacy basic type loading
                v = "[" + value.lstrip("[(").rstrip("])") + "]"
                v = yaml.load(v, Loader=yaml.SafeLoader)
                return basic_types.get(parts[0])(v)
            except Exception:
                getLogger().warning("Could not cast `{}` to basic type. Returning it as `str`".format(value))
                return value

    t = basic_types.get(str(type_str).lower().strip(), False)
    if t is not False:
        # noinspection PyBroadException
        try:
            return t(value)
        except Exception:
            return value

    return value


def get_type_from_basic_type_str(type_str: str) -> type:
    # default to str
    if not type_str:
        return str

    if str(type_str).startswith("list/"):
        v_type = list
    elif str(type_str).startswith("tuple/"):
        v_type = tuple
    elif str(type_str).startswith("dict/"):
        v_type = dict
    else:
        v_type = next(
            (t for t in (bool, int, float, str, list, tuple, dict) if t.__name__ == type_str),
            str,
        )

    return v_type


def get_basic_type(value: Any) -> str:
    basic_types = (float, int, bool, six.string_types, list, tuple, dict)

    if isinstance(value, (list, tuple)) and value:
        tv = type(value)
        t = type(value[0])
        if all(t == type(v) for v in value):
            return "{}/{}".format(str(getattr(tv, "__name__", tv)), str(getattr(t, "__name__", t)))
    elif isinstance(value, dict) and value:
        t = type(list(value.values())[0])
        if all(t == type(v) for v in value.values()):
            return "dict/{}".format(str(getattr(t, "__name__", t)))

    # it might be an empty list/dict/tuple
    t = type(value)
    if isinstance(value, basic_types):
        return str(getattr(t, "__name__", t))

    # we are storing it, even though we will not be able to restore it
    return str(getattr(t, "__name__", t))


def flatten_dictionary(a_dict: dict, prefix: str = "", sep: str = "/") -> dict:
    flat_dict = {}
    basic_types = (
        float,
        int,
        bool,
        six.string_types,
    )
    for k, v in a_dict.items():
        k = str(k)
        if isinstance(v, (float, int, bool, six.string_types)):
            flat_dict[prefix + k] = v
        elif isinstance(v, (list, tuple)) and all([isinstance(i, basic_types) for i in v]):
            flat_dict[prefix + k] = v
        elif isinstance(v, dict):
            nested_flat_dict = flatten_dictionary(v, prefix=prefix + k + sep, sep=sep)
            if nested_flat_dict:
                flat_dict.update(nested_flat_dict)
            else:
                flat_dict[k] = {}
        else:
            # this is a mixture of list and dict, or any other object,
            # leave it as is, we have nothing to do with it.
            flat_dict[prefix + k] = v
    return flat_dict


def nested_from_flat_dictionary(a_dict: dict, flat_dict: dict, prefix: str = "", sep: str = "/") -> dict:
    basic_types = (
        float,
        int,
        bool,
        six.string_types,
    )
    org_dict = copy(a_dict)
    for k, v in org_dict.items():
        k = str(k)
        if isinstance(v, (float, int, bool, six.string_types)):
            a_dict[k] = flat_dict.get(prefix + k, v)
        elif isinstance(v, (list, tuple)) and all([isinstance(i, basic_types) for i in v]):
            a_dict[k] = flat_dict.get(prefix + k, v)
        elif isinstance(v, dict):
            a_dict[k] = nested_from_flat_dictionary(v, flat_dict, prefix=prefix + k + sep, sep=sep) or v
        else:
            # this is a mixture of list and dict, or any other object,
            # leave it as is, we have nothing to do with it.
            a_dict[k] = flat_dict.get(prefix + k, v)
    return a_dict


def naive_nested_from_flat_dictionary(flat_dict: dict, sep: str = "/") -> dict:
    """A naive conversion of a flat dictionary with '/'-separated keys signifying nesting
    into a nested dictionary.
    """
    return {
        sub_prefix: (
            bucket[0][1]
            if (len(bucket) == 1 and sub_prefix == bucket[0][0])
            else naive_nested_from_flat_dictionary(
                {k[len(sub_prefix) + 1 :]: v for k, v in bucket if len(k) > len(sub_prefix)},
                sep=sep,
            )
        )
        for sub_prefix, bucket in (
            (key, list(group))
            for key, group in itertools.groupby(sorted(flat_dict.items()), key=lambda item: item[0].partition(sep)[0])
        )
    }


def walk_nested_dict_tuple_list(
    dict_list_tuple: Union[dict, tuple, list], callback: Callable[[Any], Any]
) -> Union[dict, tuple, list, Any]:
    # Do Not Change, type call will not trigger the auto resolving / download of the Lazy evaluator
    nested = (dict, tuple, list)
    type_dict_list_tuple = type(dict_list_tuple)
    if type_dict_list_tuple not in nested:
        return callback(dict_list_tuple)

    if type_dict_list_tuple == dict:
        ret = {}
        for k, v in dict_list_tuple.items():
            ret[k] = walk_nested_dict_tuple_list(v, callback=callback) if type(v) in nested else callback(v)

    else:
        ret = []
        for v in dict_list_tuple:
            ret.append(walk_nested_dict_tuple_list(v, callback=callback) if type(v) in nested else callback(v))

        if type_dict_list_tuple == tuple:
            ret = tuple(dict_list_tuple)

    return ret


class WrapperBase(type):
    # This metaclass is heavily inspired by the Object Proxying python recipe
    # (http://code.activestate.com/recipes/496741/). It adds special methods
    # to the wrapper class so it can proxy the wrapped class. In addition, it
    # adds a field __overrides__ in the wrapper class dictionary, containing
    # all attributes decorated to be overridden.

    _special_names = [
        "__abs__",
        "__add__",
        "__and__",
        "__call__",
        "__cmp__",
        "__coerce__",
        "__contains__",
        "__delitem__",
        "__delslice__",
        "__div__",
        "__divmod__",
        "__eq__",
        "__float__",
        "__floordiv__",
        "__ge__",
        "__getitem__",
        "__getslice__",
        "__gt__",
        "__hash__",
        "__hex__",
        "__iadd__",
        "__iand__",
        "__idiv__",
        "__idivmod__",
        "__ifloordiv__",
        "__ilshift__",
        "__imod__",
        "__imul__",
        "__int__",
        "__invert__",
        "__ior__",
        "__ipow__",
        "__irshift__",
        "__isub__",
        "__iter__",
        "__itruediv__",
        "__ixor__",
        "__le__",
        "__len__",
        "__long__",
        "__lshift__",
        "__lt__",
        "__mod__",
        "__mul__",
        "__ne__",
        "__neg__",
        "__oct__",
        "__or__",
        "__pos__",
        "__pow__",
        "__radd__",
        "__rand__",
        "__rdiv__",
        "__rdivmod__",
        "__reduce__",
        "__reduce_ex__",
        "__repr__",
        "__reversed__",
        "__rfloorfiv__",
        "__rlshift__",
        "__rmod__",
        "__rmul__",
        "__ror__",
        "__rpow__",
        "__rrshift__",
        "__rshift__",
        "__rsub__",
        "__rtruediv__",
        "__rxor__",
        "__setitem__",
        "__setslice__",
        "__sub__",
        "__truediv__",
        "__xor__",
        "next",
        "__str__",
        "__repr__",
        "__round__",
        "__fspath__",
        "__bytes__",
        "__index__",
    ]

    def __new__(mcs: Any, classname: str, bases: Tuple[Any], attrs: Dict[str, Any]) -> type:
        def make_method(name: str) -> Callable:
            def method(self, *args: Any, **kwargs: Any) -> Any:
                obj = object.__getattribute__(self, "_wrapped")
                if obj is None:
                    cb = object.__getattribute__(self, "_callback")
                    obj = cb()
                    object.__setattr__(self, "_wrapped", obj)

                # we have to convert the instance to the real type
                if (
                    args
                    and len(args) == 1
                    and (isinstance(args[0], LazyEvalWrapper) or hasattr(type(args[0]), "_base_class_"))
                ):
                    try:
                        int(args[0])  # force loading the instance
                    except:  # noqa
                        pass
                    args = (object.__getattribute__(args[0], "_wrapped"),)

                mtd = getattr(obj, name)
                return mtd(*args, **kwargs)

            return method

        typed_class = attrs.get("_base_class_")
        for name in mcs._special_names:
            if not typed_class or hasattr(typed_class, name):
                attrs[name] = make_method(name)

        overrides = attrs.get("__overrides__", [])
        # overrides.extend(k for k, v in attrs.items() if isinstance(v, lazy))
        attrs["__overrides__"] = overrides
        return type.__new__(mcs, classname, bases, attrs)


class LazyEvalWrapper(six.with_metaclass(WrapperBase)):
    # This class acts as a proxy for the wrapped instance it is passed. All
    # access to its attributes are delegated to the wrapped class, except
    # those contained in __overrides__.

    __slots__ = ["_wrapped", "_callback", "_remote_reference", "__weakref__"]

    _remote_reference_calls = []

    def __init__(self, callback: Callable, remote_reference: Optional[Callable] = None) -> None:
        object.__setattr__(self, "_wrapped", None)
        object.__setattr__(self, "_callback", callback)
        object.__setattr__(self, "_remote_reference", remote_reference)
        if remote_reference:
            LazyEvalWrapper._remote_reference_calls.append(remote_reference)

    def _remoteref(self) -> Any:
        func = object.__getattribute__(self, "_remote_reference")
        if func and func in LazyEvalWrapper._remote_reference_calls:
            LazyEvalWrapper._remote_reference_calls.remove(func)

        return func() if callable(func) else func

    def __getattribute__(self, attr: str) -> Any:
        if attr in ("__isabstractmethod__",):
            return None
        if attr in ("_remoteref", "_remote_reference"):
            return object.__getattribute__(self, attr)
        return getattr(LazyEvalWrapper._load_object(self), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(LazyEvalWrapper._load_object(self), attr, value)

    def __delattr__(self, attr: str) -> None:
        delattr(LazyEvalWrapper._load_object(self), attr)

    def __nonzero__(self) -> bool:
        return bool(LazyEvalWrapper._load_object(self))

    def __bool__(self) -> bool:
        return bool(LazyEvalWrapper._load_object(self))

    @staticmethod
    def _load_object(self) -> Any:
        obj = object.__getattribute__(self, "_wrapped")
        if obj is None:
            cb = object.__getattribute__(self, "_callback")
            obj = cb()
            object.__setattr__(self, "_wrapped", obj)
        return obj

    @classmethod
    def trigger_all_remote_references(cls) -> None:
        for func in cls._remote_reference_calls:
            if callable(func):
                func()
        cls._remote_reference_calls = []


def lazy_eval_wrapper_spec_class(class_type: type) -> type:
    class TypedLazyEvalWrapper(six.with_metaclass(WrapperBase)):
        _base_class_ = class_type
        __slots__ = ["_wrapped", "_callback", "__weakref__"]

        def __init__(self, callback: Callable) -> None:
            object.__setattr__(self, "_wrapped", None)
            object.__setattr__(self, "_callback", callback)

        def __nonzero__(self) -> bool:
            return bool(LazyEvalWrapper._load_object(self))

        def __bool__(self) -> bool:
            return bool(LazyEvalWrapper._load_object(self))

        def __getattribute__(self, attr: str) -> Any:
            if attr == "__isabstractmethod__":
                return None
            if attr == "__class__":
                return class_type

            return getattr(LazyEvalWrapper._load_object(self), attr)

    return TypedLazyEvalWrapper
