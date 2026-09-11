module Ref {
  @ A python array
  array PyArray = [3] U32

  @ An array of structs
  array PyStructArray = [2] PySimple

  @ A python enum
  enum PyEnum { A = 1, B = 2, C = 3 }

  @ An enum with an explicit representation type
  enum PySmallEnum: U8 { X = 0, Y = 1 }

  @ A simple struct
  struct PySimple { x: U32, y: F32 }

  @ A struct covering every member flavour
  struct PyComplex {
    x: U32
    y: string size 40
    u: PySimple
    w: PyArray
    z: PyEnum
    b: bool
    d: F64
    i: I8
    q: U64
    a: PyAlias
    s: PySimpleAlias
    ea: PyEnumAlias
    eaa: PyEnumAliasAlias
    sa: PyStringAlias
    v: [4] U32
  }

  @ An alias of a primitive
  type PyAlias = U32

  @ An alias of a struct
  type PySimpleAlias = PySimple

  @ An alias of an enum
  type PyEnumAlias = PyEnum

  @ An alias of an alias of an enum
  type PyEnumAliasAlias = PyEnumAlias

  @ An alias of a string, which names a size rather than a C++ type
  type PyStringAlias = string size 24

  @ An array of strings, whose element type is not copyable
  array PyStringArray = [2] string size 10

  @ An abstract type defined outside the model
  type PyOpaque

  port Compute(a: U32, b: PySimple) -> U32
  port Notify(ref s: PyComplex)
  port Say(msg: string size 30)
  port Describe() -> string size 24
  port Aliased(e: PyEnumAlias, s: PySimpleAlias, t: PyStringAlias)
  port SayRef(ref msg: string size 30)
  port Mixed(
    e: PyEnum
    arr: PyArray
    al: PyAlias
    sa: PySimpleAlias
    flag: bool
    big: F64
    small: I8
    t: Fw.Time
    o: PyOpaque
  ) -> PyEnum

  @ An interface whose ports are imported into a component
  interface Tickish {
    sync input port tickIn: Svc.Sched
    output port tickOut: Svc.Sched
  }

  @ fprime-python
  active component PyActive {
    import Ref.Tickish
    sync input port schedIn: Svc.Sched
    async input port pingIn: Svc.Ping
    output port pingOut: Svc.Ping
    guarded input port computeIn: Ref.Compute
    output port computeOut: Ref.Compute
    sync input port notifyIn: Ref.Notify
    sync input port sayIn: Ref.Say
    sync input port describeIn: Ref.Describe
    sync input port aliasedIn: Ref.Aliased
    sync input port sayRefIn: Ref.SayRef
    sync input port mixedIn: Ref.Mixed
    output port mixedOut: Ref.Mixed
    sync input port arrayIn: [3] Svc.Sched

    @ An internal port, dispatched off this component's own queue
    internal port deferWork(count: U32, label: string size 12)

    command recv port cmdIn
    command reg port cmdRegOut
    command resp port cmdResponseOut
    event port eventOut
    text event port textEventOut
    telemetry port tlmOut
    time get port timeGetOut
    param get port prmGetOut
    param set port prmSetOut

    @ A command with args covering every parameter flavour
    async command DO_THING(
      a: U32
      b: string size 20
      c: PyEnum
      d: PySimple
      e: PyArray
      f: bool
      g: F64
      h: PyAlias
    )
    @ A sync command
    sync command NO_ARGS()
    @ A guarded command
    guarded command GUARDED_THING(a: I16)
    @ A command taking aliases
    sync command ALIASED(e: PyEnumAlias, t: PyStringAlias)

    @ An event with args
    event THING_HAPPENED(a: U32, s: string size 40, e: PyEnum) severity activity high format "thing {} {} {}"
    @ A fatal
    event BAD severity fatal format "bad"
    @ A warning
    event WARN(x: F32) severity warning low format "warn {}"
    @ A diagnostic
    event DIAG severity diagnostic format "diag"
    @ A command-severity event
    event CMD_EVENT severity command format "cmd"
    @ A throttled event
    event THROTTLED severity activity low format "throttled" throttle 5

    @ A primitive channel
    telemetry Counter: U32
    @ A struct channel
    telemetry Complex: PyComplex
    @ An enum channel
    telemetry Mode: PyEnum
    @ An array channel
    telemetry Samples: PyArray
    @ A string channel
    telemetry Label: string size 16

    @ A primitive param
    param GAIN: F32 default 1.0
    @ A struct param
    param CFG: PySimple
    @ An enum param
    param MODE: PyEnum default PyEnum.A
    @ A string param
    param NAME: string size 16 default "n"
    @ A string-alias param
    param LABEL: PyStringAlias default "l"
  }

  @ fprime-python
  passive component PyPassive {
    sync input port schedIn: Svc.Sched
  }

  @ fprime-python
  queued component PyQueued {
    async input port pingIn: Svc.Ping
    output port pingOut: Svc.Ping
  }

  @ A component that is deliberately NOT annotated for fprime-python
  passive component PlainOld {
    sync input port schedIn: Svc.Sched
  }

  instance pyActive: PyActive base id 0x100 \
    queue size 10 stack size 4096 priority 100

  instance pyPassive: PyPassive base id 0x200

  instance pyQueued: PyQueued base id 0x300 queue size 5

  instance plainOld: PlainOld base id 0x400

  @ fprime-python
  deployment topology PyTopology {
    instance pyActive
    instance pyPassive
    instance pyQueued
    instance plainOld
    connections C {
      pyActive.pingOut -> pyActive.pingIn
      pyQueued.pingOut -> pyQueued.pingIn
    }
  }
}
