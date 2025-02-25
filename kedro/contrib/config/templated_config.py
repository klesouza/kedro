# Copyright 2018-2019 QuantumBlack Visual Analytics Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
# NONINFRINGEMENT. IN NO EVENT WILL THE LICENSOR OR OTHER CONTRIBUTORS
# BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF, OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The QuantumBlack Visual Analytics Limited ("QuantumBlack") name and logo
# (either separately or in combination, "QuantumBlack Trademarks") are
# trademarks of QuantumBlack. The License does not grant you any right or
# license to the QuantumBlack Trademarks. You may not use the QuantumBlack
# Trademarks or any confusingly similar mark as a trademark for your product,
#     or use the QuantumBlack Trademarks in any other manner that might cause
# confusion in the marketplace, including but not limited to in advertising,
# on websites, or on software.
#
# See the License for the specific language governing permissions and
# limitations under the License.
"""This module provides ``kedro.config`` with the functionality to load one
or more configuration files from specified paths, and replace template strings with default values.
"""
import re
from typing import Any, Dict, Iterable, Optional, Union

import jmespath

from kedro.config import ConfigLoader


class TemplatedConfigLoader(ConfigLoader):
    """
    Extension of the ConfigLoader class that allows for template values,
    wrapped in brackets like: ${...}, to be replaced by default values.

    The easiest way to use this class is by incorporating it into the
    ``KedroContext``. This can be done by extending the ``KedroContext`` and overwriting
    the config_loader method, making it return a ``TemplatedConfigLoader``
    object instead of a ``ConfigLoader`` object.

    For this method to work, the context_path variable in `.kedro.yml` needs
    to be pointing at this newly created class. The `run.py` script has an
    extension of the ``KedroContext`` by default, called the ``ProjectContext``.

    Example:
    ::
        >>> from kedro.context import KedroContext, load_context
        >>> from kedro.contrib.config import TemplatedConfigLoader
        >>>
        >>> class MyNewContext(KedroContext):
        >>>
        >>>    @property
        >>>    def config_loader(self) -> TemplatedConfigLoader:
        >>>        conf_paths = [
        >>>            str(self.project_path / self.CONF_ROOT / "base"),
        >>>            str(self.project_path / self.CONF_ROOT / self.env),
        >>>        ]
        >>>        return TemplatedConfigLoader(conf_paths,
        >>>                                     globals_pattern="*globals.yml",
        >>>                                     globals_dict={"param1": "CSVLocalDataSet"})

        >>> my_context = load_context(Path.cwd(), env=env)
        >>> my_context.run(tags, runner, node_names, from_nodes, to_nodes)

    The contents of the dictionary resulting from the `globals_pattern` get
    merged with the `globals_dict`. In case of conflicts, the keys in the
    `globals_dict` take precedence.

    Global parameters can be namespaced as well. An example could work as follows:

    globals.yml
    ::
        bucket: "my_s3_bucket"

        environment: "dev"

        datasets:
            csv: "CSVS3DataSet"
            spark: "SparkLocalDataSet"

        folders:
            raw: "01_raw"
            int: "02_intermediate"
            pri: "03_primary"
            fea: "04_features"


    catalog.yml
    ::
        raw_boat_data:
            type: ${datasets.spark}
            filepath: "s3a://${bucket}/${environment}/${folders.raw}/boats.csv"
            file_format: parquet

        raw_car_data:
            type: ${datasets.csv}
            filepath: "/${environment}/${folders.raw}/cars.csv"
            bucket_name: "${bucket}"

    This uses ``jmespath`` in the background. For more information see:
    https://github.com/jmespath/jmespath.py and http://jmespath.org/.
    """

    # pylint: disable=missing-type-doc
    def __init__(
        self,
        conf_paths: Union[str, Iterable[str]],
        *,
        globals_pattern: Optional[str] = None,
        globals_dict: Optional[Dict[str, Any]] = None
    ):
        """Instantiate a ``TemplatedConfigLoader``.

        Args:
            conf_paths: Non-empty path or list of paths to configuration
                directories.
            globals_pattern: Optional keyword-only argument specifying a glob
                pattern. Files that match the pattern will be loaded as a
                dictionary with default values used for replacement.
            globals_dict: Optional keyword-only argument specifying an additional
                dictionary with default values used for replacement. This
                dictionary will get merged with the globals dictionary obtained
                from the globals_pattern. In case of duplicate keys, the
                `globals_dict` keys take precedence.
        """

        super().__init__(conf_paths)

        self._arg_dict = super().get(globals_pattern) if globals_pattern else {}

        globals_dict = globals_dict or {}

        self._arg_dict = {**self._arg_dict, **globals_dict}

    def get(self, *patterns: str) -> Dict[str, Any]:
        """
        Tries to resolve the template variables in the config dictionary
        provided by the ``ConfigLoader`` (super class) `get` method using the
        dictionary of replacement values obtained in the `__init__` method.

        Args:
            patterns: Glob patterns to match. Files, which names match
                any of the specified patterns, will be processed.

        Returns:
            A Python dictionary with the combined configuration from all
                configuration files. **Note:** any keys that start with `_`
                will be ignored. String values wrapped in `${...}` will be
                replaced with the result of the corresponding JMESpath
                expression evaluated against globals (see `__init` for more
                details).
        """

        config_raw = super().get(*patterns)

        if self._arg_dict:
            return _replace_vals(config_raw, self._arg_dict)

        return config_raw


def _replace_vals(val: Any, defaults: Dict[str, Any]) -> Any:
    """
    Recursive function that loops through the values of a map. In case another
    map or a list is encountered, it calls itself. When a string is encountered,
    it will use the `defaults` dict to replace strings that look like `${expr}`,
    where `expr` is a JMESPath expression evaluated against `defaults` dict.

    Some notes on behavior:
        * If val is not a dict, list or string, the same value gets passed back.
        * If val is a string and does not match the ${...} pattern, the same
            value gets passed back.
        * If the value inside ${...} does not match any keys in the dictionary,
            the same value gets passed back.
        * If the ${...} is part of a larger string, the corresponding entry in
            the defaults dictionary gets parsed into a string and put into the
            larger string.

    Examples:
        val = '${test_key}' with defaults = {'test_key': 'test_val'} returns
            'test_val'
        val = 5 (i.e. not a dict, list or string) returns 5
        val = 'test_key' (i.e. does not match ${...} pattern returns 'test_key'
            (irrespective of defaults)
        val = '${wrong_test_key}' with defaults = {'test_key': 'test_val'}
            returns 'wrong_test_key'
        val = 'string-with-${test_key}' with defaults = {'test_key': 1000}
            returns 'string-with-1000'

    Args:
        val: If this is a string of the format ${expr}, it gets replaced
            by the result of JMESPath expression
        defaults: A lookup from string to string with replacement values

    Returns:
        Either the replacement value, if `val` is a string and was found
            in the defaults, or the original value otherwise

    """

    if isinstance(val, dict):
        return {k: _replace_vals(val[k], defaults) for k in val.keys()}

    if isinstance(val, list):
        return [_replace_vals(e, defaults) for e in val]

    if isinstance(val, str):
        # Distinguish case where entire string matches the pattern,
        # as the replacement can be of a different type
        pattern_full = r"^\$\{([^\}]*)\}$"
        match_full = re.search(pattern_full, val)
        if match_full:
            return jmespath.search(match_full.group(1), defaults) or val

        pattern_partial = r"\$\{([^\}]*)\}"
        return re.sub(
            pattern_partial,
            lambda m: str(jmespath.search(m.group(1), defaults)) or m.group(0),
            val,
        )
    return val
