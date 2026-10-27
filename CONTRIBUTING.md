# Contributing

Use synthetic fixtures and offline tests. Do not submit customer projects,
real recipes, site addresses, controller routes, captures, screenshots or credentials.
Describe the behavior and validation of each change. Python tests run with
`python -m unittest discover -s tests -v` after installing the package.

For TwinCAT changes also run `tests/Test-Offline.ps1` on Windows. Test code must
never connect to a controller. XML parsing is not a TwinCAT build; macro linting
is not an EasyBuilder Pro compilation.
