---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.16.6
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

import importlib
from polar.storage import duckdb as duckdb_import
from polar.utils.config import load_configuration

importlib.reload(duckdb_import)

# Load configuration
config = load_configuration()

glob_patterns = ["Anton_Antonov*.CSV"]

summary = duckdb_import.import_workout_from_directory(glob_patterns, config)
summary
```

```{code-cell} ipython3
import sys
from pathlib import Path

# Add src to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

import importlib
from polar.storage import duckdb as duckdb_import
from polar.utils.config import load_configuration
importlib.reload(duckdb_import)

# Load configuration
config = load_configuration()

duckdb_import.delete_workout_by_id('04-10-2025_090750', config)
```
