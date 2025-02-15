import builtins
import typing as ty
import numbers
from immutabledict import immutabledict

import strax

export, __all__ = strax.exporter()

# Placeholder value for omitted values.
# Use instead of None since None might be a proper value/default
OMITTED = "<OMITTED>"
__all__.extend("OMITTED InvalidConfiguration".split())


@export
class InvalidConfiguration(Exception):
    pass


@export
def takes_config(*options):
    """Decorator for plugin classes, to specify which options it takes.

    :param options: Option instances of options this plugin takes.

    """

    def wrapped(plugin_class):
        result = {}
        for opt in options:
            if not isinstance(opt, Option):
                raise RuntimeError("Specify config options by Option objects")
            opt.taken_by = plugin_class.__name__
            result[opt.name] = opt

        # For some reason the second condition is essential, I don't understand
        # yet why...
        if hasattr(plugin_class, "takes_config") and len(plugin_class.takes_config):
            # Already have some options set, e.g. because of subclassing
            # where both child and parent have a takes_config decorator
            for opt in result.values():
                if opt.name in plugin_class.takes_config:
                    raise RuntimeError(f"Attempt to specify option {opt.name} twice")
            plugin_class.takes_config = immutabledict({**plugin_class.takes_config, **result})
        else:
            plugin_class.takes_config = immutabledict(result)
        if isinstance(opt, strax.Config):
            setattr(plugin_class, opt.name, opt)
        return plugin_class

    return wrapped


@export
class Option:
    """Configuration option taken by a strax plugin."""

    taken_by: str

    def __init__(
        self,
        name: str,
        type: ty.Union[str, type, tuple, list] = OMITTED,
        default: ty.Any = OMITTED,
        default_factory: ty.Union[str, ty.Callable] = OMITTED,
        default_by_run=OMITTED,
        child_option: bool = False,
        parent_option_name: ty.Optional[str] = None,
        track: bool = True,
        infer_type=False,
        help: str = "",
    ):
        """
        :param name: Option identifier
        :param type: Excepted type of the option's value.
        :param default: Default value the option takes.
        :param default_factory: Function that produces a default value.
        :param default_by_run: Specify that default is run-dependent. Either
            - Callable. Will be called with run_id, must return value for run.
            - List [(start_run_id, value), ..,] for values specified by range of runs.
            "default_by_run" can only be usd in contexts where the context option
            "use_per_run_defaults" is set to True
        :param child_option: If true option is marked as a child_option. All
            options which are marked as a child overwrite the corresponding parent
            option. Removes also the corresponding parent option from the lineage.
        :param parent_option_name: Name of the parent option of child option.
            Required to find the key of the parent option so it can be overwritten
            by the value of the child option.
        :param track: If True (default), option value becomes part of plugin
        lineage (just like the plugin version).
        :param infer_type: Whether to infer the type from the
            default value if type not explicitly set.
        :param help: Human-readable description of the option.
        """
        self.name = name
        self.type = type
        self.default = default
        self.default_by_run = default_by_run
        self.default_factory = default_factory
        self.track = track
        self.help = help

        # Options required for inherited child plugins:
        # Require both to be more explicit and reduce errors by the user
        self.child_option = child_option
        self.parent_option_name = parent_option_name
        if (self.child_option and not self.parent_option_name) or (
            not self.child_option and self.parent_option_name
        ):
            raise ValueError(
                'You have to specify both, "child_option"=True and '
                "the name of the parent option which should be "
                "overwritten by the child. Options which are unique "
                "to the child should not be marked as a child option."
                f"Please update {self.name} accordingly."
            )

        # if self.default_by_run is not OMITTED:
        #     warnings.warn(f"The {self.name} option uses default_by_run,"
        #                   f" which will soon stop working!",
        #                   DeprecationWarning)

        if (
            sum([
                self.default is not OMITTED,
                self.default_factory is not OMITTED,
                self.default_by_run is not OMITTED,
            ])
            > 1
        ):
            raise RuntimeError(f"Tried to specify more than one default for option {self.name}.")

        if infer_type and type is OMITTED and default is not OMITTED:
            for ntype in [numbers.Integral, numbers.Number]:
                # first check if its a number otherwise numpy numbers
                # will fail type checking when checked against int and float.
                # numbers.Integral, numbers.Number are safe to use
                # since numpy registers them as super-classes.
                # left as a loop since we may want to add other exceptions as
                # they are discovered.
                if isinstance(default, ntype):
                    self.type = ntype
                    break
            else:
                self.type = builtins.type(default)

    def get_default(self, run_id, run_defaults: ty.Optional[dict] = None):
        """Return default value for the option."""
        if run_defaults is not None and self.name in run_defaults:
            return run_defaults[self.name]
        if self.default is not OMITTED:
            return self.default
        if self.default_factory is not OMITTED:
            return self.default_factory()  # type: ignore

        if self.default_by_run is not OMITTED:
            # TODO: This legacy code for handling default_per_run will soon
            # be removed!
            if run_id is None:
                run_id = 0  # TODO: think if this makes sense

            if isinstance(run_id, str):
                is_superrun = run_id.startswith("_")
                if not is_superrun:
                    run_id = int(run_id.replace("_", ""))
            else:
                is_superrun = False

            if callable(self.default_by_run):
                raise RuntimeError(
                    "Using functions to specify per-run defaults is no longer"
                    "supported: specify a (first_run, option) list, or "
                    "a URL of a file to process in the plugin"
                )

            if is_superrun:
                return "<SUBRUN-DEPENDENT:%s>" % strax.deterministic_hash(self.default_by_run)

            use_value = OMITTED
            for i, (start_run, value) in enumerate(self.default_by_run):
                if start_run > run_id:
                    break
                use_value = value
            if use_value is OMITTED:
                raise ValueError(
                    f"Run id {run_id} is smaller than the "
                    "lowest run id {start_run} for which the default "
                    "of the option {self.name} is known."
                )
            return use_value

        raise InvalidConfiguration(f"Missing option {self.name} required by {self.taken_by}")

    def validate(
        self,
        config,
        run_id=None,  # TODO: will soon be removed
        run_defaults=None,
        set_defaults=True,
    ):
        """Checks if the option is in config and sets defaults if needed."""
        if self.name in config:
            value = config[self.name]
            if self.type is not OMITTED and not isinstance(value, self.type):
                # TODO replace back with InvalidConfiguration
                UserWarning(
                    f"Invalid type for option {self.name}. "
                    f"Excepted a {self.type}, got a {type(value)}"
                )
        elif set_defaults:
            config[self.name] = self.get_default(run_id, run_defaults)


# subclass Option for backward compatibility
@export
class Config(Option):
    """An alternative to the `takes_config` class decorator which uses the descriptor protocol to
    return the config value when the attribute is accessed from within a plugin."""

    def __init__(self, **kwargs):
        # for now set the name to empty string
        # will be replaced by the actual name
        # after __set_name__ is called on class
        # instantiation
        if "name" not in kwargs:
            kwargs["name"] = ""
        super().__init__(**kwargs)

    def __set_name__(self, owner, name):
        """'Plugin class has been instantiated we can now set the option name and add it to the
        plugins takes_config dictionary."""
        self.name = name
        self.taken_by = owner.__name__
        new_takes_config = {name: self}
        if hasattr(owner, "takes_config") and len(owner.takes_config):
            # Already have some options set, e.g. because of subclassing
            # where both child and parent have a takes_config decorator

            if name in owner.takes_config:
                raise RuntimeError(f"Attempt to specify option {name} twice")
            owner.takes_config = immutabledict({**owner.takes_config, **new_takes_config})
        else:
            owner.takes_config = immutabledict(new_takes_config)

    def __get__(self, obj, objtype=None):
        return self.fetch(obj)

    def __set__(self, obj, value):
        raise AttributeError(f"{self.name} is a plugin configuration and cannot be set directly.")

    def fetch(self, plugin):
        """This function is called when the attribute is being accessed.

        Should be overridden by subclasses to customize behavior.

        """
        if not hasattr(plugin, "config"):
            raise AttributeError("Plugin has not been configured.")
        if self.name in plugin.config:
            return plugin.config[self.name]
        return self.get_default(plugin.run_id)


@export
def combine_configs(old_config, new_config=None, mode="update"):
    if new_config is None:
        new_config = dict()

    if mode == "update":
        c = old_config.copy()
        c.update(new_config)
        return c
    if mode == "setdefault":
        return combine_configs(new_config, old_config, mode="update")
    if mode == "replace":
        return new_config

    raise RuntimeError("Expected update, setdefault or replace as config setting mode")
