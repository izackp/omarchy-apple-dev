# device-run.sh --lldb, one command, breakpoint hit, 2026-10-07

Host: a 16-inch M1 Max MacBook Pro (Arch Linux aarch64), iPhone 15 Pro Max (iPhone16,2) iOS 27.0.1 (24A446) through the
Studio Display hub, Swift 6.4.0 swift.org toolchain, xtool f0a1f90. Started 13:46Z with
`LLDB_CMDS` holding the five LLDB commands below; the app is the `xtool new` template (UO).

```
$ LLDB_PYTHONHOME=<cpython 3.12> LLDB_CMDS=<commands> device-run.sh --lldb
== 4. Build, sign, install, launch ==
Successfully installed!
== 5. LLDB ==
   Sysroot: ~/.cache/omarchy-apple-dev/DeviceSupport/27.0.1 (24A446)
(lldb) breakpoint set --file ContentView.swift --line 7
Breakpoint 1: 2 locations.
(lldb) script lldb.debugger.SetAsync(False)
(lldb) continue
* thread #1, queue = 'com.apple.main-thread', stop reason = breakpoint 1.3
      frame #0: 0x0000000104fc00a0 UO`ContentView.body.getter() at ContentView.swift:7:23
   5   	        let greeting = "Hello from Omarchy Linux"
   6   	        let launches = 42
-> 7   	        return VStack {
(lldb) frame variable greeting launches
(String) greeting = "Hello from Omarchy Linux"
(Int) launches = 42
```

The DDI mount, the RSD tunnel, the phone's Swift runtime (copied once, 6.9 GB, reused) and the
sysroot all came from the script. `thread backtrace 5` printed only the idle worker thread
(thread #5 is the selected thread after the stop; the stop was on thread #1).
