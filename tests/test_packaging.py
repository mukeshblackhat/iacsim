"""Data files must ship with the package (a non-editable `pip install .` needs them)."""

from importlib import resources


def test_defaults_yaml_is_package_data():
    assert resources.files("iacsim.latency").joinpath("defaults.yaml").is_file()


def test_viewer_html_is_package_data():
    assert resources.files("iacsim.viewer").joinpath("index.html").is_file()
