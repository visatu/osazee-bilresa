# Development

Install pinned test dependencies with `python -m pip install -r tests/requirements.txt`,
then run `python -B -m unittest discover -s tests -v` from the repository root.

The tests load the actual blueprint YAML and execute its Jinja/actions against a small
simulated light registry. They do not call Home Assistant services or simulate physical
light transitions. Validate the blueprint in the minimum supported Home Assistant
version and test with the actual remote and lights before release.
