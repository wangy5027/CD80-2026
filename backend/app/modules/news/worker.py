"""Trusted subprocess entry point; stdout contains only the public news JSON."""

from contextlib import redirect_stdout
import json
import sys

from provider import SOURCES, normalize_records


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SOURCES:
        return 2
    source = sys.argv[1]
    try:
        with redirect_stdout(sys.stderr):
            import akshare as ak
            frame = (ak.stock_info_global_em() if source == "eastmoney"
                     else ak.stock_info_global_cls(symbol="全部"))
            items = normalize_records(source, frame.to_dict(orient="records"))
        print(json.dumps({"items": items}, ensure_ascii=False, allow_nan=False))
        return 0
    except ModuleNotFoundError:
        print(json.dumps({"error": "dependency_missing"}))
        return 1
    except Exception:
        # Do not return upstream errors, request URLs or environment details.
        print(json.dumps({"error": "upstream_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
