# Working on this project

## "Done" means preflight passed

Before saying any change is done, start (or restart) the server and run the live preflight:

```bash
python3 server.py &          # restart it if server.py or build.py changed
python3 preflight.py
```

Paste its final summary line (`N pass, N fail, N warn`) in the reply. A change is only done when
preflight exits 0. If it fails, fix the cause and run it again; never describe what it would say
instead of running it. If a failure cannot be fixed from here (for example, no valid API key in
config.json), say so plainly and paste the line anyway.

preflight.py makes real calls to the running server and the Anthropic API. It is not a unit test
suite and must never be given mocks.
