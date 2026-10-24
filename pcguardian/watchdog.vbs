' PCGuardian 看门狗：每分钟由计划任务调用，若 guardian 进程(sysguard.exe)被结束则自动拉起
Option Explicit
Dim sh, wmi, procs, dir
Set sh = CreateObject("WScript.Shell")
dir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
If Not wmi Is Nothing Then
  Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='sysguard.exe'")
  If procs.Count = 0 Then
    sh.CurrentDirectory = dir
    sh.Run """" & dir & "sysguard.exe"" """ & dir & "guardian.pyw""", 0, False
  End If
End If
