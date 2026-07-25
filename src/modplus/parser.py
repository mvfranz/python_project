"""Recursive-descent parser for the modplus language."""

from __future__ import annotations

from . import ast_nodes as A
from .errors import ParseError, SourcePos
from .lexer import Token, TokKind, tokenize

_TYPE_KEYWORDS = {"INTEGER", "REAL", "BOOLEAN", "CHAR"}
_STMT_START_KEYWORDS = {
    "IF",
    "WHILE",
    "REPEAT",
    "FOR",
    "RETURN",
    "NEW",
    "DISPOSE",
}
_SECTION_KEYWORDS = {"CONST", "TYPE", "VAR", "PROCEDURE", "GENERIC"}
_BLOCK_END_KEYWORDS = {"END", "ELSE", "ELSIF", "UNTIL"}


class Parser:
    def __init__(self, source: str) -> None:
        self.tokens: list[Token] = tokenize(source)
        self.i = 0

    # -- token helpers -----------------------------------------------------

    def _tok(self, offset: int = 0) -> Token:
        j = self.i + offset
        if j >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[j]

    def _pos(self) -> SourcePos:
        return self._tok().pos

    def _advance(self) -> Token:
        tok = self._tok()
        if self.i < len(self.tokens) - 1:
            self.i += 1
        return tok

    def _at_kw(self, *names: str) -> bool:
        tok = self._tok()
        return tok.kind == TokKind.KEYWORD and tok.text in names

    def _at_op(self, *ops: str) -> bool:
        tok = self._tok()
        return tok.kind == TokKind.OP and tok.text in ops

    def _expect_op(self, op: str) -> Token:
        if not self._at_op(op):
            raise ParseError(f"expected {op!r}, got {self._tok().text!r}", self._pos())
        return self._advance()

    def _expect_kw(self, kw: str) -> Token:
        if not self._at_kw(kw):
            raise ParseError(f"expected {kw!r}, got {self._tok().text!r}", self._pos())
        return self._advance()

    def _expect_ident(self) -> str:
        tok = self._tok()
        if tok.kind != TokKind.IDENT:
            raise ParseError(f"expected identifier, got {tok.text!r}", tok.pos)
        return self._advance().text

    def _checkpoint(self) -> int:
        return self.i

    def _restore(self, checkpoint: int) -> None:
        self.i = checkpoint

    # -- entry point ---------------------------------------------------

    def parse_module(self) -> A.Module:
        pos = self._pos()
        self._expect_kw("MODULE")
        name = self._expect_ident()
        self._expect_op(";")
        imports = self._parse_imports()
        consts, types, vars_, procs = self._parse_decl_sections(top_level=True)
        self._expect_kw("BEGIN")
        body = self._parse_stmt_seq()
        self._expect_kw("END")
        end_name = self._expect_ident()
        if end_name != name:
            raise ParseError(
                f"mismatched module end name: expected {name!r}, got {end_name!r}", self._pos()
            )
        self._expect_op(".")
        return A.Module(name, imports, consts, types, vars_, procs, body, pos)

    def _parse_imports(self) -> list[A.ImportedName]:
        imports: list[A.ImportedName] = []
        while self._at_kw("IMPORT"):
            self._advance()
            while True:
                ipos = self._pos()
                imports.append(A.ImportedName(self._expect_ident(), ipos))
                if self._at_op(","):
                    self._advance()
                    continue
                break
            self._expect_op(";")
        return imports

    # -- declarations ----------------------------------------------------

    def _parse_decl_sections(
        self, top_level: bool
    ) -> tuple[list[A.ConstDecl], list[A.TypeDecl], list[A.VarDecl], list[A.ProcDecl]]:
        consts: list[A.ConstDecl] = []
        types: list[A.TypeDecl] = []
        vars_: list[A.VarDecl] = []
        procs: list[A.ProcDecl] = []
        while True:
            if self._at_kw("CONST"):
                consts.extend(self._parse_const_section())
            elif self._at_kw("TYPE"):
                types.extend(self._parse_type_section(top_level))
            elif self._at_kw("VAR"):
                vars_.extend(self._parse_var_section())
            elif self._at_kw("PROCEDURE", "GENERIC"):
                procs.append(self._parse_proc_decl(top_level))
            else:
                break
        return consts, types, vars_, procs

    def _parse_const_section(self) -> list[A.ConstDecl]:
        self._expect_kw("CONST")
        out = []
        while self._tok().kind == TokKind.IDENT:
            pos = self._pos()
            name = self._expect_ident()
            self._expect_op("=")
            value = self._parse_expr()
            self._expect_op(";")
            out.append(A.ConstDecl(name, value, pos))
        return out

    def _parse_type_section(self, top_level: bool) -> list[A.TypeDecl]:
        self._expect_kw("TYPE")
        out = []
        while self._tok().kind == TokKind.IDENT:
            pos = self._pos()
            name = self._expect_ident()
            type_params: list[A.TypeParam] = []
            if self._at_op("<"):
                if not top_level:
                    raise ParseError(
                        "generic type declarations are only allowed at module level", pos
                    )
                type_params = self._parse_type_param_list()
            self._expect_op("=")
            ty = self._parse_type()
            self._expect_op(";")
            out.append(A.TypeDecl(name, type_params, ty, pos))
        return out

    def _parse_var_section(self) -> list[A.VarDecl]:
        self._expect_kw("VAR")
        out = []
        while self._tok().kind == TokKind.IDENT:
            pos = self._pos()
            names = self._parse_ident_list()
            self._expect_op(":")
            ty = self._parse_type()
            self._expect_op(";")
            out.append(A.VarDecl(names, ty, pos))
        return out

    def _parse_ident_list(self) -> list[str]:
        names = [self._expect_ident()]
        while self._at_op(","):
            self._advance()
            names.append(self._expect_ident())
        return names

    def _parse_type_param_list(self) -> list[A.TypeParam]:
        self._expect_op("<")
        params = []
        while True:
            pos = self._pos()
            name = self._expect_ident()
            const_type = None
            if self._at_op(":"):
                self._advance()
                self._expect_kw("CONST")
                const_type = self._parse_type()
            params.append(A.TypeParam(name, const_type, pos))
            if self._at_op(","):
                self._advance()
                continue
            break
        self._expect_op(">")
        return params

    def _parse_proc_decl(self, top_level: bool) -> A.ProcDecl:
        pos = self._pos()
        is_generic_kw = self._at_kw("GENERIC")
        if is_generic_kw:
            self._advance()
            if not top_level:
                raise ParseError("GENERIC PROCEDURE is only allowed at module level", pos)
        self._expect_kw("PROCEDURE")
        name = self._expect_ident()

        type_params: list[A.TypeParam] = []
        specializes_args: list[A.TypeArg] | None = None
        if is_generic_kw:
            type_params = self._parse_type_param_list()
        elif self._at_op("<"):
            if not top_level:
                raise ParseError(
                    "explicit template specializations are only allowed at module level", pos
                )
            specializes_args = self._parse_type_arg_list()

        self._expect_op("(")
        params = self._parse_formal_params()
        self._expect_op(")")
        ret_type = None
        if self._at_op(":"):
            self._advance()
            ret_type = self._parse_type()
        self._expect_op(";")

        consts, types, vars_, nested_procs = self._parse_decl_sections(top_level=False)
        self._expect_kw("BEGIN")
        body = self._parse_stmt_seq()
        self._expect_kw("END")
        end_name = self._expect_ident()
        if end_name != name:
            raise ParseError(
                f"mismatched procedure end name: expected {name!r}, got {end_name!r}", self._pos()
            )
        self._expect_op(";")
        return A.ProcDecl(
            name,
            type_params,
            params,
            ret_type,
            consts,
            types,
            vars_,
            body,
            pos,
            specializes_args,
            nested_procs,
        )

    def _parse_formal_params(self) -> list[A.Param]:
        params: list[A.Param] = []
        if self._at_op(")"):
            return params
        while True:
            pos = self._pos()
            by_ref = False
            if self._at_kw("VAR"):
                self._advance()
                by_ref = True
            names = self._parse_ident_list()
            self._expect_op(":")
            ty = self._parse_type()
            params.append(A.Param(names, ty, by_ref, pos))
            if self._at_op(";"):
                self._advance()
                continue
            break
        return params

    # -- types -------------------------------------------------------------

    def _parse_type(self) -> A.TypeExpr:
        pos = self._pos()
        if self._at_kw("ARRAY"):
            self._advance()
            self._expect_op("[")
            size = self._parse_expr()
            self._expect_op("]")
            self._expect_kw("OF")
            elem = self._parse_type()
            return A.ArrayType(size, elem, pos)
        if self._at_kw("RECORD"):
            self._advance()
            fields = []
            while self._tok().kind == TokKind.IDENT:
                fpos = self._pos()
                names = self._parse_ident_list()
                self._expect_op(":")
                fty = self._parse_type()
                self._expect_op(";")
                fields.append(A.FieldDecl(names, fty, fpos))
            self._expect_kw("END")
            return A.RecordType(fields, pos)
        if self._at_kw("OWN"):
            self._advance()
            self._expect_kw("POINTER")
            self._expect_kw("TO")
            base = self._parse_type()
            return A.PointerType(base, True, pos)
        if self._at_kw("POINTER"):
            self._advance()
            self._expect_kw("TO")
            base = self._parse_type()
            return A.PointerType(base, False, pos)
        if self._at_kw(*_TYPE_KEYWORDS):
            name = self._advance().text
            return A.NamedType(name, pos)
        if self._tok().kind == TokKind.IDENT:
            name = self._advance().text
            if self._at_op("<"):
                args = self._parse_type_arg_list()
                return A.GenericInstanceType(name, args, pos)
            return A.NamedType(name, pos)
        raise ParseError(f"expected a type, got {self._tok().text!r}", pos)

    def _parse_type_arg_list(self) -> list[A.TypeArg]:
        self._expect_op("<")
        args: list[A.TypeArg] = []
        while True:
            args.append(self._parse_type_arg())
            if self._at_op(","):
                self._advance()
                continue
            break
        self._expect_op(">")
        return args

    def _parse_type_arg(self) -> A.TypeArg:
        # A non-type (constant) template argument is a literal integer or a
        # previously-declared CONST identifier; anything else is a type.
        if self._tok().kind == TokKind.INT_LIT:
            pos = self._pos()
            return A.IntLit(int(self._advance().text), pos)
        if self._at_op("-") and self._tok(1).kind == TokKind.INT_LIT:
            pos = self._pos()
            self._advance()
            return A.IntLit(-int(self._advance().text), pos)
        return self._parse_type()

    # -- statements ----------------------------------------------------

    def _parse_stmt_seq(self) -> list[A.Stmt]:
        if self._at_kw(*_BLOCK_END_KEYWORDS):
            return []
        stmts = [self._parse_stmt()]
        while self._at_op(";"):
            self._advance()
            if self._at_kw(*_BLOCK_END_KEYWORDS):
                break
            stmts.append(self._parse_stmt())
        return stmts

    def _parse_stmt(self) -> A.Stmt:
        pos = self._pos()
        if self._at_kw("IF"):
            return self._parse_if()
        if self._at_kw("WHILE"):
            return self._parse_while()
        if self._at_kw("REPEAT"):
            return self._parse_repeat()
        if self._at_kw("FOR"):
            return self._parse_for()
        if self._at_kw("RETURN"):
            self._advance()
            value = None
            if not self._at_op(";") and not self._at_kw(*_BLOCK_END_KEYWORDS):
                value = self._parse_expr()
            return A.Return(value, pos)
        if self._at_kw("NEW"):
            self._advance()
            self._expect_op("(")
            target = self._parse_designator_only("NEW")
            self._expect_op(")")
            return A.NewStmt(target, pos)
        if self._at_kw("DISPOSE"):
            self._advance()
            self._expect_op("(")
            target = self._parse_designator_only("DISPOSE")
            self._expect_op(")")
            return A.DisposeStmt(target, pos)
        if self._tok().kind == TokKind.IDENT:
            designator = self._parse_designator()
            if self._at_op(":="):
                self._advance()
                if not isinstance(designator, A.Designator):
                    raise ParseError("the left side of ':=' must be a variable", pos)
                value = self._parse_expr()
                return A.Assign(designator, value, pos)
            if isinstance(designator, A.Call):
                return A.CallStmt(designator, pos)
            if isinstance(designator, A.Designator) and not designator.parts:
                # A bare name in statement position is a parameterless call,
                # e.g. `WriteLn;` (Modula-2 style: no empty `()` required).
                return A.CallStmt(A.Call(designator.name, None, [], designator.pos), pos)
            raise ParseError("expected ':=' or a procedure call", self._pos())
        raise ParseError(f"unexpected token {self._tok().text!r} in statement", pos)

    def _parse_if(self) -> A.If:
        pos = self._pos()
        self._expect_kw("IF")
        branches = []
        cond = self._parse_expr()
        self._expect_kw("THEN")
        body = self._parse_stmt_seq()
        branches.append(A.IfBranch(cond, body))
        while self._at_kw("ELSIF"):
            self._advance()
            cond = self._parse_expr()
            self._expect_kw("THEN")
            body = self._parse_stmt_seq()
            branches.append(A.IfBranch(cond, body))
        else_body = None
        if self._at_kw("ELSE"):
            self._advance()
            else_body = self._parse_stmt_seq()
        self._expect_kw("END")
        return A.If(branches, else_body, pos)

    def _parse_while(self) -> A.While:
        pos = self._pos()
        self._expect_kw("WHILE")
        cond = self._parse_expr()
        self._expect_kw("DO")
        body = self._parse_stmt_seq()
        self._expect_kw("END")
        return A.While(cond, body, pos)

    def _parse_repeat(self) -> A.Repeat:
        pos = self._pos()
        self._expect_kw("REPEAT")
        body = self._parse_stmt_seq()
        self._expect_kw("UNTIL")
        cond = self._parse_expr()
        return A.Repeat(body, cond, pos)

    def _parse_for(self) -> A.For:
        pos = self._pos()
        self._expect_kw("FOR")
        var = self._expect_ident()
        self._expect_op(":=")
        start = self._parse_expr()
        self._expect_kw("TO")
        stop = self._parse_expr()
        step = None
        if self._at_kw("BY"):
            self._advance()
            step = self._parse_expr()
        self._expect_kw("DO")
        body = self._parse_stmt_seq()
        self._expect_kw("END")
        return A.For(var, start, stop, step, body, pos)

    # -- expressions ---------------------------------------------------

    _REL_OPS = ("=", "#", "<", "<=", ">", ">=", "<>")
    _ADD_OPS = ("+", "-")
    _MUL_OPS = ("*", "/")

    def _parse_expr(self) -> A.Expr:
        left = self._parse_simple_expr()
        if self._at_op(*self._REL_OPS):
            pos = self._pos()
            op = self._advance().text
            right = self._parse_simple_expr()
            return A.BinOp(op, left, right, pos)
        return left

    def _parse_simple_expr(self) -> A.Expr:
        pos = self._pos()
        neg = False
        if self._at_op("+", "-"):
            neg = self._advance().text == "-"
        term = self._parse_term()
        left: A.Expr = A.UnaryOp("-", term, pos) if neg else term
        while self._at_op(*self._ADD_OPS) or self._at_kw("OR"):
            op_pos = self._pos()
            op = self._advance().text
            right = self._parse_term()
            left = A.BinOp(op, left, right, op_pos)
        return left

    def _parse_term(self) -> A.Expr:
        left = self._parse_factor()
        while self._at_op(*self._MUL_OPS) or self._at_kw("DIV", "MOD", "AND"):
            pos = self._pos()
            op = self._advance().text
            right = self._parse_factor()
            left = A.BinOp(op, left, right, pos)
        return left

    def _parse_factor(self) -> A.Expr:
        pos = self._pos()
        tok = self._tok()
        if tok.kind == TokKind.INT_LIT:
            self._advance()
            return A.IntLit(int(tok.text), pos)
        if tok.kind == TokKind.REAL_LIT:
            self._advance()
            return A.RealLit(float(tok.text), pos)
        if tok.kind == TokKind.CHAR_LIT:
            self._advance()
            return A.CharLit(tok.text, pos)
        if tok.kind == TokKind.KEYWORD and tok.text == "TRUE":
            self._advance()
            return A.BoolLit(True, pos)
        if tok.kind == TokKind.KEYWORD and tok.text == "FALSE":
            self._advance()
            return A.BoolLit(False, pos)
        if tok.kind == TokKind.KEYWORD and tok.text == "NIL":
            self._advance()
            return A.NilLit(pos)
        if tok.kind == TokKind.KEYWORD and tok.text == "NOT":
            self._advance()
            operand = self._parse_factor()
            return A.UnaryOp("NOT", operand, pos)
        if self._at_op("("):
            self._advance()
            inner = self._parse_expr()
            self._expect_op(")")
            return inner
        if tok.kind == TokKind.IDENT:
            return self._parse_designator()
        raise ParseError(f"unexpected token {tok.text!r} in expression", pos)

    def _parse_paren_args(self) -> list[A.Expr]:
        self._expect_op("(")
        args = []
        if not self._at_op(")"):
            args.append(self._parse_expr())
            while self._at_op(","):
                self._advance()
                args.append(self._parse_expr())
        self._expect_op(")")
        return args

    def _parse_designator_only(self, context: str) -> A.Designator:
        pos = self._pos()
        result = self._parse_designator()
        if not isinstance(result, A.Designator):
            raise ParseError(f"{context} requires a variable, not a procedure call", pos)
        return result

    def _parse_designator(self) -> A.Expr:
        pos = self._pos()
        name = self._expect_ident()

        type_args: list[A.TypeArg] | None = None
        if self._at_op("<"):
            checkpoint = self._checkpoint()
            try:
                candidate_args = self._parse_type_arg_list()
                if self._at_op("("):
                    type_args = candidate_args
                else:
                    self._restore(checkpoint)
            except ParseError:
                self._restore(checkpoint)

        if self._at_op("("):
            args = self._parse_paren_args()
            return A.Call(name, type_args, args, pos)

        # A qualified call to an imported module's procedure, e.g.
        # `Foo.Bar(...)` or (rejected later, with a clear message, by
        # sema.py -- generic templates cannot yet be imported) `Foo.Bar<...>(...)`.
        # A qualified *value* reference (`Foo.SomeVar`, not followed by a
        # call) is intentionally left to fall through to the ordinary
        # parts loop below as `Designator("Foo", [FieldAccess("SomeVar")])`
        # -- the parser can't yet tell "Foo" is a module rather than a
        # RECORD-valued variable; sema disambiguates that once it can look
        # names up.
        if type_args is None and self._at_op(".") and self._tok(1).kind == TokKind.IDENT:
            checkpoint = self._checkpoint()
            self._advance()  # '.'
            member = self._expect_ident()
            member_type_args: list[A.TypeArg] | None = None
            if self._at_op("<"):
                inner_checkpoint = self._checkpoint()
                try:
                    candidate = self._parse_type_arg_list()
                    if self._at_op("("):
                        member_type_args = candidate
                    else:
                        self._restore(inner_checkpoint)
                except ParseError:
                    self._restore(inner_checkpoint)
            if self._at_op("("):
                args = self._parse_paren_args()
                return A.Call(member, member_type_args, args, pos, qualifier=name)
            self._restore(checkpoint)

        parts: list[A.DesignatorPart] = []
        while True:
            if self._at_op("."):
                self._advance()
                fpos = self._pos()
                fname = self._expect_ident()
                parts.append(A.FieldAccess(fname, fpos))
            elif self._at_op("["):
                self._advance()
                ipos = self._pos()
                idx = self._parse_expr()
                self._expect_op("]")
                parts.append(A.IndexAccess(idx, ipos))
            elif self._at_op("^"):
                dpos = self._pos()
                self._advance()
                parts.append(A.Deref(dpos))
            else:
                break
        return A.Designator(name, parts, pos)


def parse(source: str) -> A.Module:
    return Parser(source).parse_module()
