#!/usr/bin/env python
"""probe_fpp.py -- empirical map of the OLD fprime_python_model API onto the NEW `fpp` bindings.

Run with the venv interpreter that has fprime-fpp-python installed:

    /Users/tumbar/git/fprime-python/.venv/bin/python \
        /Users/tumbar/git/fprime-python/tests/fixtures/probe_fpp.py

Every section is labelled with the letter of the corresponding question in the port
investigation (A..O).  Nothing here is guessed: each print is the observed value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fpp

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "Ref.fpp"
IMPORTS = Path("/tmp/imports.txt")

FPRIME_PYTHON_ANNOTATION = "fprime-python"


# --------------------------------------------------------------------------------------
# helpers the real port will want
# --------------------------------------------------------------------------------------

def enum_name(value) -> str:
    """Name of a pyo3 'enum' member.

    fpp's enums (ComponentKind, IntegerKind, EventSeverity, ...) are *native pyo3*
    classes, NOT `enum.Enum` (despite what __init__.pyi claims), so there is no
    `.name` / `.value`.  `str()` yields 'ClassName.Member'.
    """
    return str(value).rsplit(".", 1)[-1]


#: EventSeverity -> F Prime C++ token.  Replaces the old
#: `{"activity high": "ACTIVITY_HI", ...}` string map.  Note `Command`, which the
#: old map did not have.
SEVERITY_TO_CPP = {
    fpp.EventSeverity.ActivityHigh: "ACTIVITY_HI",
    fpp.EventSeverity.ActivityLow: "ACTIVITY_LO",
    fpp.EventSeverity.Command: "COMMAND",
    fpp.EventSeverity.Diagnostic: "DIAGNOSTIC",
    fpp.EventSeverity.Fatal: "FATAL",
    fpp.EventSeverity.WarningHigh: "WARNING_HI",
    fpp.EventSeverity.WarningLow: "WARNING_LO",
}

#: Symbol class -> generated-include suffix (old IncludeManager map).
SYMBOL_TO_INCLUDE_SUFFIX = {
    fpp.SymbolAliasType: "Alias",
    fpp.Array: "Array",
    fpp.SymbolComponent: "Component",
    fpp.Constant: "Constant",
    fpp.SymbolEnum: "Enum",
    fpp.Port: "Port",
    fpp.Struct: "Serializable",
    fpp.SymbolTopology: "Topology",
}


def cpp_name(ty, *, string_placeholder: str = "string") -> str:
    """The C++ type token for a semantic `fpp.Type`.

    This is the replacement for the old `DataTypeDataHelper.cpp_type`
    (`str(field)` for primitives / `Symbol.construct(...)` + qualified name for
    everything else).  `str(ty)` is NOT usable in the new API: it returns
    '<Type PrimitiveInt>'.
    """
    if ty is None:
        return "void"
    # Order matters: an alias of a primitive reports is_primitive == True.
    if isinstance(ty, fpp.TypeAliasType):
        return ty.def_symbol.qualified_name.replace(".", "::")
    if isinstance(ty, fpp.PrimitiveInt):
        return enum_name(ty.value)                      # -> 'U32'
    if isinstance(ty, fpp.Float):
        return enum_name(ty.value)                      # -> 'F32'
    if isinstance(ty, fpp.TypeBoolean):
        return "bool"
    if isinstance(ty, fpp.TypeString):
        return string_placeholder                       # size is ty.value (Optional[int])
    if isinstance(ty, fpp.TypeInteger):
        return "I64"                                    # unbounded integer literal type
    if isinstance(ty, fpp.EnumType):
        return ty.def_symbol.qualified_name.replace(".", "::")
    if isinstance(ty, (fpp.ArrayType, fpp.StructType, fpp.TypeAbsType)):
        return ty.def_symbol.qualified_name.replace(".", "::")
    raise TypeError(f"no cpp name rule for {type(ty).__name__}: {ty!r}")


def struct_members_in_order(struct_type: fpp.StructType):
    """(name, Type, inline_array_size_or_None) in DECLARATION order.

    `StructType.anon_struct.members` is a hash map and does NOT preserve
    declaration order; `StructType.node.members` (the DefStruct) does.
    """
    members = struct_type.anon_struct.members
    sizes = struct_type.sizes                            # plain str keys now
    return [
        (m.name, members[m.name], sizes.get(m.name))
        for m in struct_type.node.members
    ]


def port_classification(pi) -> dict:
    """Full classification of one entry of Component.port_map."""
    if isinstance(pi, fpp.GeneralPortInstance):
        category = "general"
        # GeneralKind is a UNION of unit classes (AsyncInput/SyncInput/GuardedInput/Output),
        # NOT an enum -- so the discriminator is the Python class name / isinstance.
        sync = type(pi.kind).__name__
    elif isinstance(pi, fpp.SpecialPortInstance):
        category = "special"
        sync = None
    elif isinstance(pi, fpp.InternalPortInstance):
        category = "internal"
        sync = "Async"                                   # internal ports are always async
    else:
        category = type(pi).__name__
        sync = None
    return {
        "name": pi.unqualified_name,
        "class": type(pi).__name__,
        "category": category,
        "direction": None if pi.direction is None else enum_name(pi.direction),
        "sync_kind": sync,
        "special_kind": None if pi.special_kind is None else enum_name(pi.special_kind),
        "is_async_input": pi.is_async_input,
        "array_size": pi.array_size,
        "imported": bool(pi.import_node_ids),
    }


def port_names_in_declaration_order(component) -> list[str]:
    """Names of component.port_map in source order, expanding interface imports.

    `Component.port_map` is a hash map (deterministic across runs, but arbitrary
    order).  `Component.node.members` is in source order but contains
    `SpecInterfaceImport` instead of the imported ports, so it has to be expanded.
    """
    names: list[str] = []

    def walk(members):
        for member in members:
            if isinstance(member, fpp.SpecInterfaceImport):
                sym = member.interface.definition          # -> SymbolInterface
                walk(sym.definition.members)               # -> DefInterface.members
            elif isinstance(member, (fpp.SpecGeneralPortInstance,
                                     fpp.SpecSpecialPortInstance,
                                     fpp.SpecInternalPort)):
                names.append(member.name)

    walk(component.node.members)
    return names


def def_port_of(pi):
    """DefPort behind a port instance, or None for a serial port."""
    ty = pi.type
    if isinstance(ty, fpp.PortInstanceTypeDefPort):
        return ty.value.definition
    return None


def annotations_of(node) -> list[str]:
    return list(node.pre_annotation) + list(node.post_annotation)


def is_fprime_python(node) -> bool:
    return FPRIME_PYTHON_ANNOTATION in [a.strip() for a in annotations_of(node)]


# --------------------------------------------------------------------------------------

def hdr(letter: str, text: str) -> None:
    print()
    print("=" * 100)
    print(f"[{letter}] {text}")
    print("=" * 100)


def main() -> int:
    # ---------------------------------------------------------------- load  (O, __main__)
    hdr("O", "analyze(): no imports-vs-sources distinction; m.ast / TransUnit.uri")
    paths = IMPORTS.read_text().split() + [str(FIXTURE)]
    model = fpp.analyze(paths)
    assert not model.has_errors, [ (d.level, d.message) for d in model.diagnostics ]
    print("has_errors      :", model.has_errors, " error_count:", model.error_count)
    print("len(model.ast)  :", len(model.ast), "== len(inputs):", len(paths))
    for tu in model.ast:
        print(f"  TransUnit.uri = {tu.uri}   ({len(tu.members)} top-level members)")
    try:
        fpp.analyze(paths, imports=[])            # type: ignore[call-arg]
    except TypeError as exc:
        print("analyze(..., imports=[]) ->", type(exc).__name__, exc)
    print("NOTE: analyze() takes ONE flat list. Nothing marks a path as import-only;")
    print("      filter generated output yourself by TransUnit.uri / node.location.uri.")

    analysis = model.analysis

    # ---------------------------------------------------------------- A: annotations
    hdr("A", "Annotations on DefComponent / DefTopology (pre + post)")
    for qn in ("Ref.PyActive", "Ref.PyPassive", "Ref.PyQueued", "Ref.PyTopology", "Ref.PyArray"):
        d = model.lookup(qn).definition
        print(f"{qn:16s} pre={d.pre_annotation!r:35s} post={d.post_annotation!r:6s} "
              f"is_fprime_python={is_fprime_python(d)}")
    print("=> list[str]; leading '@' and surrounding whitespace ARE stripped by fpp.")
    print("   Empty list (not None) when unannotated. Trailing '@<' annotations land in")
    print("   post_annotation, so the port must still concatenate both lists.")

    # ---------------------------------------------------------------- B: component kind
    hdr("B", "Component kind: active / passive / queued")
    for qn in ("Ref.PyActive", "Ref.PyPassive", "Ref.PyQueued"):
        comp = analysis.component_map[model.lookup(qn)]
        k = comp.node.kind
        print(f"{qn:16s} comp.node.kind = {k!r}  str={str(k)!r}  enum_name={enum_name(k)!r}  "
              f"== ComponentKind.Active: {k == fpp.ComponentKind.Active}")
    print("=> fpp.ComponentKind has NO .name/.value (not a real enum.Enum). Compare with")
    print("   `is`/`==` against fpp.ComponentKind.{Active,Passive,Queued}.")

    # ---------------------------------------------------------------- C: port classification
    hdr("C", "Full port_map of Ref.PyActive, classified")
    active = analysis.component_map[model.lookup("Ref.PyActive")]
    ordered = port_names_in_declaration_order(active)
    assert sorted(ordered) == sorted(active.port_map), (ordered, list(active.port_map))
    print(f"{'name':16s} {'class':22s} {'cat':9s} {'dir':7s} {'sync':13s} "
          f"{'special_kind':13s} {'async_in':9s} imported  DefPort")
    for name in ordered:
        pi = active.port_map[name]
        c = port_classification(pi)
        dp = def_port_of(pi)
        print(f"{c['name']:16s} {c['class']:22s} {c['category']:9s} "
              f"{str(c['direction']):7s} {str(c['sync_kind']):13s} "
              f"{str(c['special_kind']):13s} {str(c['is_async_input']):9s} "
              f"{str(c['imported']):9s} {pi.type.value.qualified_name if dp else '(serial)'}")
    print()
    print("port_map KEY ORDER (arbitrary hash order, deterministic across runs but NOT")
    print("declaration order):", list(active.port_map.keys()))
    print("declaration order:", ordered)
    print("=> Component.node.members is source-ordered but holds SpecInterfaceImport in")
    print("   place of imported ports -> expand via")
    print("   SpecInterfaceImport.interface.definition.definition.members.")
    print("   Imported ports are flagged by a non-empty PortInstance.import_node_ids;")
    print("   PortInstance.import_locs gives the import site, pi.node.location the")
    print("   defining spec inside the interface:")
    for name in ordered:
        pi = active.port_map[name]
        if pi.import_node_ids:
            print(f"     {name}: import_node_ids={pi.import_node_ids} "
                  f"import_locs={[str(s) for s in pi.import_locs]} "
                  f"node.location={pi.node.location!r}")
    print()
    print("special_port_map keys:", [enum_name(k) for k in active.special_port_map.keys()])
    print("has time-get port  :", fpp.SpecialPortInstanceKind.TimeGet in active.special_port_map)
    print("has_commands/events/telemetry/parameters:",
          active.has_commands, active.has_events, active.has_telemetry, active.has_parameters)

    # internal port: not in the Ref fixture, probe it in memory
    internal_model = fpp.analyze(
        IMPORTS.read_text().split(),
        source=(
            "module Inc {\n"
            "  active component C {\n"
            "    internal port myInternal(a: U32) priority 5\n"
            "    async input port p: Svc.Sched\n"
            "  }\n"
            "}\n"
        ),
        uri="<internal-probe>",
    )
    assert not internal_model.has_errors, internal_model.diagnostics
    inc = internal_model.analysis.component_map[internal_model.lookup("Inc.C")]
    print()
    for pi in inc.port_map.values():
        print("  internal probe:", port_classification(pi))
        if isinstance(pi, fpp.InternalPortInstance):
            print("     InternalPortInstance.node.params:",
                  [(p.name, cpp_name(p.type_name.resolved_type)) for p in pi.node.params],
                  " priority:", pi.priority, " queue_full:", enum_name(pi.queue_full),
                  " pi.type:", pi.type)
    print("=> internal ports: direction is None, special_kind is None, .type is None;")
    print("   they carry their own params on SpecInternalPort (no DefPort).")

    # ---------------------------------------------------------------- D: port signature
    hdr("D", "Port signature: DefPort formal params + return type, with semantic Types")
    for name in ("schedIn", "pingIn", "computeIn", "computeOut", "notifyIn", "timeGetOut", "cmdIn"):
        pi = active.port_map[name]
        dp = def_port_of(pi)
        print(f"-- {name}: port symbol = {pi.type.value.qualified_name}  DefPort={dp.name}")
        print(f"   port_returns_value span: {pi.type.port_returns_value!r}")
        for p in dp.params:
            t = p.type_name.resolved_type
            print(f"     param {p.name:8s} kind={enum_name(p.kind):5s} "
                  f"Type={type(t).__name__:14s} cpp={cpp_name(t)!r}")
        rt = dp.return_type
        rty = None if rt is None else rt.resolved_type
        print(f"     return: node={rt!r} Type={type(rty).__name__ if rty else None} "
              f"cpp={cpp_name(rty)!r}")

    # ---------------------------------------------------------------- E: C++ type names
    hdr("E", "C++ type name from a semantic Type (every distinct type in the fixture)")
    seen: dict[str, tuple] = {}

    def record(label, t):
        seen.setdefault(label, (type(t).__name__, cpp_name(t),
                                t.is_primitive,
                                getattr(t, "value", None) if isinstance(t, fpp.TypeString) else None,
                                None if t.def_symbol is None else t.def_symbol.qualified_name))

    for qn in ("Ref.PyArray", "Ref.PyEnum", "Ref.PySimple", "Ref.PyComplex", "Ref.PyAlias"):
        record(qn, analysis.type_map[model.lookup(qn).node])
    complex_t = analysis.type_map[model.lookup("Ref.PyComplex").node]
    for mname, mtype, msize in struct_members_in_order(complex_t):
        record(f"PyComplex.{mname}", mtype)
    simple_t = analysis.type_map[model.lookup("Ref.PySimple").node]
    for mname, mtype, _ in struct_members_in_order(simple_t):
        record(f"PySimple.{mname}", mtype)
    record("Fw.Time (abstract)", def_port_of(active.port_map["timeGetOut"]).params[0].type_name.resolved_type)
    record("FwOpcodeType (alias)", def_port_of(active.port_map["cmdIn"]).params[0].type_name.resolved_type)
    record("Fw.CmdArgBuffer (abstract)", def_port_of(active.port_map["cmdIn"]).params[2].type_name.resolved_type)
    bool_model = fpp.analyze(source="module B { struct S { a: bool, b: F64, c: string } }")
    for mname, mtype in bool_model.analysis.type_map[bool_model.lookup("B.S").node].anon_struct.members.items():
        record(f"B.S.{mname}", mtype)

    print(f"{'label':28s} {'Type class':16s} {'cpp_name()':22s} is_prim str_size def_symbol")
    for label, (cls, cpp, prim, ssize, dsym) in seen.items():
        print(f"{label:28s} {cls:16s} {cpp:22s} {str(prim):8s} {str(ssize):8s} {dsym}")
    print()
    print("'U32'          <- enum_name(PrimitiveInt.value)  (str(IntegerKind.U32)='IntegerKind.U32')")
    print("'Ref::PySimple'<- StructType.def_symbol.qualified_name.replace('.','::')")
    print("string detect  <- isinstance(t, fpp.TypeString)")
    print("BEWARE: TypeAliasType.is_primitive is True for `type PyAlias = U32`, so the")
    print("        alias check MUST come before the is_primitive check.")
    print("Also: Def<X>.resolved_type gives the semantic Type straight off the definition")
    print("      node, no analysis.type_map lookup needed:")
    for qn in ("Ref.PySimple", "Ref.PyEnum", "Ref.PyArray", "Ref.PyAlias"):
        print(f"      {qn:16s} definition.resolved_type = {model.lookup(qn).definition.resolved_type!r}")

    # ------------------------------------------------------------ E': string sizes
    hdr("E'", "string size N: TypeString.value is populated ONLY in some positions")

    def string_size_via_ast(type_name_node, an=analysis):
        """`string size N` recovered from the AST when TypeString.value is None."""
        kind = type_name_node.kind
        assert isinstance(kind, fpp.TypeNameString)
        return None if kind.value is None else an.get_int_value(kind.value)

    def report_string(label, semantic_type, type_name_node, an=analysis):
        try:
            ss = semantic_type.serialized_size
        except ValueError as exc:
            ss = f"raises ValueError({exc})"
        print(f"  {label:34s} TypeString.value={str(semantic_type.value):5s} "
              f"serialized_size={str(ss):32s} via AST={string_size_via_ast(type_name_node, an)}")

    complex_y = [x for x in complex_t.node.members if x.name == "y"][0]
    report_string("struct member PyComplex.y (40)",
                  complex_y.type_name.resolved_type, complex_y.type_name)
    do_thing = active.command_map[0]
    cmd_b = [p for p in do_thing.node.params if p.name == "b"][0]
    report_string("command param DO_THING.b (20)",
                  cmd_b.type_name.resolved_type, cmd_b.type_name)
    ev_s = [p for p in active.event_map[0].node.params if p.name == "s"][0]
    report_string("event param THING_HAPPENED.s (40)",
                  ev_s.type_name.resolved_type, ev_s.type_name)

    smodel = fpp.analyze(
        IMPORTS.read_text().split(),
        source=(
            "module Q {\n"
            "  port P(s: string size 33)\n"
            "  active component C {\n"
            "    async input port pin: Q.P\n"
            "    telemetry T: string size 55\n"
            "    param PP: string size 77\n"
            "    command recv port ci\n    command reg port cr\n"
            "    command resp port cro\n    param get port pg\n"
            "    param set port ps\n    telemetry port to\n"
            "    time get port tg\n  }\n}\n"
        ),
        uri="<string-size-probe>",
    )
    assert not smodel.has_errors, smodel.diagnostics
    sa = smodel.analysis
    sc = sa.component_map[smodel.lookup("Q.C")]
    sprm = sc.param_map[0]
    report_string("component param Q.C.PP (77)", sprm.param_type, sprm.node.type_name, sa)
    stlm = sc.tlm_channel_map[0]
    report_string("tlm channel Q.C.T (55)", stlm.channel_type, stlm.node.type_name, sa)
    sport = sc.port_map["pin"].type.value.definition.params[0]
    report_string("port formal param Q.P.s (33)",
                  sport.type_name.resolved_type, sport.type_name, sa)
    amodel = fpp.analyze(source="module Z { array A = [2] string size 9\n type AL = string size 13 }")
    az = amodel.analysis
    aelt = az.type_map[amodel.lookup("Z.A").node].anon_array.elt_type
    print(f"  {'array element Z.A elt (9)':34s} TypeString.value={aelt.value}")
    aal = az.type_map[amodel.lookup("Z.AL").node].underlying_type
    print(f"  {'alias underlying Z.AL (13)':34s} TypeString.value={aal.value}")
    print("=> populated for struct members / array elements / alias targets;")
    print("   ALWAYS None for FormalParam, SpecParam and SpecTlmChannel string types,")
    print("   and .serialized_size raises ValueError('Unavailable') for those.")
    print("   Workaround: TypeName.kind.value (Expr) -> analysis.get_int_value(expr).")

    # ---------------------------------------------------------------- F: commands
    hdr("F", "Commands: real commands vs param set/save pseudo-commands")
    for op, cmd in sorted(active.command_map.items()):
        if isinstance(cmd, fpp.NonParam):
            params = [(p.name, enum_name(p.kind), cpp_name(p.type_name.resolved_type))
                      for p in cmd.node.params]
            print(f"opcode {op}: REAL  {cmd.name:14s} kind={enum_name(cmd.kind):8s} "
                  f"is_async={cmd.is_async} params={params}")
        else:
            print(f"opcode {op}: PSEUDO {cmd.name:14s} ParamKind={enum_name(cmd.kind):5s} "
                  f"(from param {cmd.node.name})")
    real = [c for c in active.command_map.values() if isinstance(c, fpp.NonParam)]
    print("=> filter: isinstance(cmd, fpp.NonParam);  real commands:", [c.name for c in real])

    # ---------------------------------------------------------------- G: events
    hdr("G", "Events: severity as C++ token + params")
    for eid, ev in sorted(active.event_map.items()):
        sev = ev.node.severity
        print(f"id {eid}: {ev.name:16s} severity={str(sev):28s} cpp={SEVERITY_TO_CPP[sev]:12s} "
              f"log_method=log_{SEVERITY_TO_CPP[sev]}_{ev.name}")
        for p in ev.node.params:
            print(f"      param {p.name:6s} {cpp_name(p.type_name.resolved_type)}")

    # ---------------------------------------------------------------- H: params + tlm
    hdr("H", "Params and telemetry channels: name + semantic type")
    for pid, prm in sorted(active.param_map.items()):
        print(f"param id {pid}: {prm.name:8s} param_type={type(prm.param_type).__name__:12s} "
              f"cpp={cpp_name(prm.param_type):16s} default={prm.default!r} "
              f"set/save opcode={prm.set_opcode}/{prm.save_opcode} external={prm.is_external}")
    for cid, ch in sorted(active.tlm_channel_map.items()):
        print(f"chan id {cid}: {ch.name:8s} channel_type={type(ch.channel_type).__name__:12s} "
              f"cpp={cpp_name(ch.channel_type):16s} update={enum_name(ch.update)}")
    print("tlm_channel_name_map keys:", sorted(active.tlm_channel_name_map.keys()))

    # ---------------------------------------------------------------- I: struct members
    hdr("I", "Struct members: declaration order, Types, inline arrays")
    for qn in ("Ref.PySimple", "Ref.PyComplex"):
        st = analysis.type_map[model.lookup(qn).node]
        print(f"-- {qn}")
        print("   anon_struct.members key order :", list(st.anon_struct.members.keys()))
        print("   node.members (DECLARATION)    :", [m.name for m in st.node.members])
        print("   sizes (inline arrays)         :", st.sizes)
        for mname, mtype, msize in struct_members_in_order(st):
            tag = f"INLINE ARRAY[{msize}] -> SKIP" if msize is not None else ""
            print(f"     {mname:4s} {type(mtype).__name__:14s} cpp={cpp_name(mtype):18s} {tag}")
    print("=> anon_struct.members does NOT preserve declaration order. Use node.members.")
    print("=> StructType.sizes is dict[str, int] now (old code needed fpp_ast.Unqualified(name)).")

    # ---------------------------------------------------------------- J: enum constants
    hdr("J", "Enum constants in order")
    et = analysis.type_map[model.lookup("Ref.PyEnum").node]
    print("constants:", [c.name for c in et.node.constants])
    print("with values:", [(c.name, None if c.value is None else c.value.resolved_value.value)
                           for c in et.node.constants])
    print("rep_type:", enum_name(et.rep_type),
          " default:", None if et.default is None else et.default.value)
    print("=> DefEnum.constants is a list[DefEnumConstant] in declaration order; each has")
    print("   .name directly (old code needed the Annotated 3-tuple + .data.name).")

    # ---------------------------------------------------------------- K: qualified names
    hdr("K", "Qualified names (FPP dotted + C++ ::)")
    for qn in ("Ref.PyActive", "Ref.PyComplex", "Ref.PyTopology", "Ref.pyActive"):
        sym = model.lookup(qn)
        print(f"{qn:16s} sym.qualified_name={sym.qualified_name:16s} "
              f"analysis.get_qualified_name={analysis.get_qualified_name(sym):16s} "
              f"unqualified={sym.unqualified_name:12s} cpp={sym.qualified_name.replace('.','::')}")
    top = analysis.topology_map[model.lookup("Ref.PyTopology")]
    print("Topology.qualified_name :", top.qualified_name)
    print("Topology.unqualified_name/.name :", top.unqualified_name, "/", top.name)
    print("Component.symbol.qualified_name :", active.symbol.qualified_name)

    # ---------------------------------------------------------------- L: source locations
    hdr("L", "Source locations: defining .fpp file, and TU membership")
    for qn in ("Ref.PyActive", "Ref.PyArray", "Ref.Compute", "Ref.PyTopology",
               "Svc.Sched", "Fw.Time", "FwOpcodeType"):
        sym = model.lookup(qn)
        loc = sym.definition.location
        suffix = SYMBOL_TO_INCLUDE_SUFFIX.get(type(sym))
        inc = (Path(loc.uri).parent / f"{sym.unqualified_name}{suffix}Ac.hpp") if suffix else None
        print(f"{qn:16s} {type(sym).__name__:24s} loc.uri={loc.uri}")
        print(f"{'':16s} line={loc.line} (0-indexed) col={loc.column}  include={inc}")
    print()
    print("None-location audit over every AST node in the model:")
    counter = {"n": 0, "none": 0}

    class LocAudit(fpp.NodeVisitor):
        def generic_visit(self, node):
            counter["n"] += 1
            if node.location is None:
                counter["none"] += 1
            super().generic_visit(node)

    audit = LocAudit()
    for tu in model.ast:
        for mem in tu.members:
            audit.visit(mem)
    print(f"  visited {counter['n']} nodes, {counter['none']} with location is None")
    print("=> AstNode.location is typed Optional[Loc] but is populated for every parsed node.")
    print("   Loc.uri is a str (canonicalised: /private/tmp/... on macOS) while TransUnit.uri")
    print("   is the path as given -> always compare Path(...).resolve().")

    # include-splice behaviour: an included definition reports the INCLUDED file
    inc_dir = Path("/tmp/fpp_probe_include")
    inc_dir.mkdir(exist_ok=True)
    (inc_dir / "Inner.fppi").write_text("array IncArray = [2] U32\n")
    (inc_dir / "Outer.fpp").write_text('module Inc2 {\n  include "Inner.fppi"\n  array Own = [1] U32\n}\n')
    im = fpp.analyze([str(inc_dir / "Outer.fpp")])
    print()
    print("include-splice probe: TransUnit.uri =", im.ast[0].uri)
    for mem in im.ast[0].members:
        for sub in mem.members:
            print(f"   {sub.name:10s} location.uri={sub.location.uri}")
    print("=> a spliced definition's location.uri is the .fppi, not the TU -> exactly the")
    print("   old `location_map[node_id].file` semantics.")

    # ---------------------------------------------------------------- M: topology
    hdr("M", "Topology: instances whose COMPONENT is @ fprime-python")
    print("Topology.qualified_name:", top.qualified_name)
    print("instance_map order:", [i.unqualified_name for i in top.instance_map])
    for inst in top.instance_map:
        if not isinstance(inst, fpp.InterfaceInstanceComponent):
            print("  (skip non-component instance)", type(inst).__name__)
            continue
        comp = inst.component
        annotated = is_fprime_python(comp.node)
        print(f"  {inst.unqualified_name:10s} qualified={inst.qualified_name:16s} "
              f"cpp={inst.qualified_name.replace('.','::'):18s} "
              f"component={comp.symbol.qualified_name:16s} kind={enum_name(comp.node.kind):8s} "
              f"annotations={annotations_of(comp.node)} -> bind={annotated}")
    print("topology namespace for codegen:", "::".join(top.qualified_name.split(".")[:-1]))
    print("is_deployment:", top.symbol.definition.is_deployment)

    # ---------------------------------------------------------------- N: symbol kinds
    hdr("N", "Symbol kind discrimination for include-file suffixes")
    for qn in ("Ref.PyAlias", "Ref.PyArray", "Ref.PyActive", "Ref.PyEnum", "Ref.Compute",
               "Ref.PySimple", "Ref.PyTopology", "Ref.pyActive", "Ref"):
        sym = model.lookup(qn)
        print(f"{qn:16s} {type(sym).__name__:24s} suffix={SYMBOL_TO_INCLUDE_SUFFIX.get(type(sym))!r:14s} "
              f"repr={sym!r}")
    print("=> isinstance / type() against the concrete Symbol subclasses. Note the")
    print("   INCONSISTENT naming: SymbolAliasType/SymbolComponent/SymbolEnum/SymbolTopology")
    print("   but bare Array/Constant/Port/Struct/Module/System/EnumConstant.")
    kmodel = fpp.analyze(source="module K { constant FOO = 7\n array A = [FOO] U32 }")
    ksym = kmodel.lookup("K.FOO")
    print(f"  constant probe: {type(ksym).__name__} {ksym!r} isinstance(fpp.Constant)="
          f"{isinstance(ksym, fpp.Constant)} value={ksym.definition.value.resolved_value.value}")
    print("The union TypeAliases in __all__ DO exist at runtime as types.UnionType, so")
    print("isinstance works with them too (the old `type_filter=PortInstance` idiom):")
    pi_sched = active.port_map["schedIn"]
    print("  isinstance(pi, fpp.PortInstance)  =", isinstance(pi_sched, fpp.PortInstance))
    print("  isinstance(cmd, fpp.Command)      =", isinstance(active.command_map[0], fpp.Command))
    print("  isinstance(sym, fpp.Symbol)       =", isinstance(model.lookup("Ref"), fpp.Symbol))
    print("  ...or the *Base classes: PortInstanceBase / CommandBase / SymbolBase / TypeBase")

    # ---------------------------------------------------------------- visitor.py port
    hdr("visitor.py", "NodeVisitor replacement for AstVisitor")

    class Collector(fpp.NodeVisitor):
        def __init__(self):
            self.hits = []

        def visit_DefModule(self, node):
            self.hits.append(("module", node.name))
            super().visit_DefModule(node)          # keep descending

        def visit_DefArray(self, node):
            self.hits.append(("array", node.name))

        def visit_DefEnum(self, node):
            self.hits.append(("enum", node.name))

        def visit_DefStruct(self, node):
            self.hits.append(("struct", node.name))

        def visit_DefAliasType(self, node):
            self.hits.append(("alias", node.name))

        def visit_DefComponent(self, node):
            self.hits.append(("component", node.name, is_fprime_python(node)))
            # do NOT call super(): prune, exactly like the old visitor did

        def visit_DefTopology(self, node):
            self.hits.append(("topology", node.name, is_fprime_python(node)))

    collector = Collector()
    fixture_tu = model.ast[-1]
    for mem in fixture_tu.members:
        collector.visit(mem)
    for h in collector.hits:
        print("  ", h)
    try:
        Collector().visit(fixture_tu)
    except TypeError as exc:
        print("visit(TransUnit) ->", type(exc).__name__, exc)
    print("=> traversal is in DECLARATION order. You must loop TransUnit.members yourself;")
    print("   visit() rejects a TransUnit (the stub docstring example `v.visit(root)` for")
    print("   `root in model.ast()` is wrong on both counts).")

    # ---------------------------------------------------------------- .node vs .a_node
    hdr("binding_generator", "DataHelper.get_annotated_node(): .node vs .a_node dance")
    print("Old API had some objects with .node and some with .a_node (an Annotated 3-tuple).")
    print("In the new API every analysis object exposes .node -> a plain AstNode ...")
    probes = [
        ("Component", active),
        ("NonParam(command)", active.command_map[0]),
        ("CommandParam(pseudo)", active.command_map[2]),
        ("Event", active.event_map[0]),
        ("Param", active.param_map[0]),
        ("TlmChannel", active.tlm_channel_map[0]),
        ("GeneralPortInstance", active.port_map["schedIn"]),
        ("SpecialPortInstance", active.port_map["timeGetOut"]),
        ("Interface", analysis.interface_map[model.lookup("Ref.Tickish")]),
        ("InterfaceInstanceComponent", next(iter(top.instance_map))),
    ]
    for label, obj in probes:
        print(f"  {label:28s} .node -> {getattr(obj, 'node', '<MISSING>')!r}")
    print(f"  {'Topology':28s} .node -> "
          f"{'<MISSING>' if not hasattr(top, 'node') else top.node!r}"
          f"   (use topology.symbol.definition -> {top.symbol.definition!r})")
    print("  annotations come off the AstNode: .pre_annotation / .post_annotation")

    # ---------------------------------------------------------------- interfaces
    hdr("interface", "DefInterface / Interface (new since the old autocoder)")
    for sym, iface in analysis.interface_map.items():
        print(f"  {sym.qualified_name}: node={iface.node!r} annotations={annotations_of(iface.node)}")
        print("    DefInterface.members:",
              [(type(x).__name__, getattr(x, 'name', '?')) for x in iface.node.members])
        print("    Interface.port_interface.port_map:", list(iface.port_interface.port_map.keys()))
        print("    imports_in_source_order:", iface.imports_in_source_order)

    # ---------------------------------------------------------------- patch_analysis
    hdr("__main__.patch_analysis", "no longer needed")
    print("The old code built node_id -> analysis-object maps because the visitor only had")
    print("AST node ids. In the new API you go the other way round:")
    print("  DefComponent -> Symbol : node.definition is None for a *definition*;")
    print("                           use model.lookup(qualified_name) or build")
    print("                           {sym.node: sym for sym in analysis.component_map}")
    print("  analysis.symbol_map is ALREADY node_id -> Symbol (len %d) -- this single map"
          % len(analysis.symbol_map))
    print("  replaces the whole patch_analysis() dance for definitions:")
    for qn in ("Ref.PyArray", "Ref.PyEnum", "Ref.PySimple", "Ref.PyComplex", "Ref.PyAlias",
               "Ref.Compute", "Ref.PyActive", "Ref.PyTopology", "Ref.Tickish", "Ref.pyActive"):
        sym = model.lookup(qn)
        print(f"     symbol_map[{sym.node:4d}] = {analysis.symbol_map[sym.node]!r}")
    comp_def = model.lookup("Ref.PyActive").definition
    print("  round trip DefComponent#%d -> symbol_map -> component_map -> %r" % (
        comp_def.node_id, analysis.component_map[analysis.symbol_map[comp_def.node_id]]))
    print("  (AstNode.definition is the *use-site* resolver and is None on a definition:",
          comp_def.definition, ")")
    print("  Command/Event/Param/TlmChannel/PortInstance all expose .node (an AstNode) and")
    print("  .name directly, so no node_id -> object index is required at all.")

    print()
    print("ALL PROBES COMPLETED OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
