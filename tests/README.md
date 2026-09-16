# Tests

Run the whole suite from the repository root, on the app's own environment (`.venv`, made on the first start), since the tests import the same packages the app does:

```
.venv/bin/python -m unittest discover -s tests -t .
```

None of the tests make any network calls: anything that would leave the machine is replaced with a stand-in, and everything else - the server, the run machinery, the checks - runs for real against a throwaway data folder.
