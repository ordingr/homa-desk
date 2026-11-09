import sys

import homa.mcp_stdio

if sys.stderr is not None:
    print("python -m assistant.mcp_stdio is now python -m homa.mcp_stdio (this alias is removed in 0.3.0)",
          file=sys.stderr)
sys.exit(homa.mcp_stdio.main())
