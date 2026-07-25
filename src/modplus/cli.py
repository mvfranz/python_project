"""modplusc: command-line driver for the modplus compiler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import ModplusError
from .jit import compile_to_llvm_ir, compile_to_object, run


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="modplusc", description="The modplus compiler.")
    parser.add_argument(
        "command",
        choices=["run", "emit-llvm", "emit-object"],
        help="what to do with the source file",
    )
    parser.add_argument("file", type=Path, help="a .m2p source file")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="output path for emit-llvm/emit-object"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        source = args.file.read_text(encoding="utf-8")
    except OSError as e:
        print(f"modplusc: cannot read {args.file}: {e}", file=sys.stderr)
        return 1

    module_name = args.file.stem
    try:
        if args.command == "run":
            return run(source, module_name)
        if args.command == "emit-llvm":
            ir_text = compile_to_llvm_ir(source, module_name)
            if args.output:
                args.output.write_text(ir_text, encoding="utf-8")
            else:
                print(ir_text, end="")
            return 0
        if args.command == "emit-object":
            obj_bytes = compile_to_object(source, module_name)
            output = args.output or args.file.with_suffix(".o")
            output.write_bytes(obj_bytes)
            return 0
    except ModplusError as e:
        print(f"modplusc: {e}", file=sys.stderr)
        return 1
    return 1  # pragma: no cover - unreachable, argparse restricts `command`


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
