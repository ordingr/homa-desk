import sys

import homa.__main__

if sys.stderr is not None:
    print("python -m assistant is now python -m homa (this alias is removed in 0.3.0)", file=sys.stderr)
sys.exit(homa.__main__.main())
