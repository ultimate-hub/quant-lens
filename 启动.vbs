' ============================================================
'  量化终端 v2 - 一键启动
'  双击本文件，会打开一个独立的软件窗口（不是浏览器）
' ============================================================

Option Explicit

Dim shell, fso, baseDir, py, pyw, i

Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = baseDir

' ---------- 1. 找 Python ----------
py = PickPython()

If py = "" Then
    MsgBox "没有找到 Python。" & vbCrLf & vbCrLf & _
           "请先安装 Python 3.10 或更高版本（安装时勾选 Add Python to PATH）：" & vbCrLf & _
           "https://www.python.org/downloads/", _
           vbCritical, "量化终端 v2"
    shell.Run "https://www.python.org/downloads/", 1, False
    WScript.Quit 1
End If

' pythonw.exe = 无控制台版本，不会闪黑窗口
pyw = ToPythonW(py)

' ---------- 2. 检查依赖 ----------
If Not HasDeps(py) Then
    MsgBox "首次运行需要安装运行库（约 200MB，3-5 分钟）。" & vbCrLf & vbCrLf & _
           "点“确定”后自动安装，请耐心等待，不要关闭弹出的窗口。", _
           vbInformation, "量化终端 v2 - 首次运行"
    shell.Run """" & py & """ -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple", 1, True

    If Not HasDeps(py) Then
        MsgBox "依赖安装失败。" & vbCrLf & vbCrLf & _
               "手动安装方法：打开命令行进入本文件夹，执行：" & vbCrLf & _
               "  " & py & " -m pip install -r requirements.txt", _
               vbCritical, "量化终端 v2"
        WScript.Quit 1
    End If
End If

' ---------- 3. 启动独立窗口（隐藏控制台）----------
'    desktop_qt.py 内部会启动本地服务并打开 Qt 窗口
On Error Resume Next
shell.Run """" & pyw & """ """ & baseDir & "\desktop_qt.py""", 0, False
If Err.Number <> 0 Then
    MsgBox "启动失败：" & Err.Description & vbCrLf & vbCrLf & _
           "请把本文件夹里的 运行日志.log 发给开发者。", _
           vbCritical, "量化终端 v2"
    On Error GoTo 0
    WScript.Quit 1
End If
On Error GoTo 0


' ============================================================
'  辅助函数
' ============================================================

' 把 python.exe 换成同目录的 pythonw.exe（无控制台）
Function ToPythonW(p)
    Dim fso2, folder, f
    Set fso2 = CreateObject("Scripting.FileSystemObject")
    If InStr(p, "\") > 0 Then
        If fso2.FileExists(p) Then
            folder = fso2.GetParentFolderName(p)
            f = folder & "\pythonw.exe"
            If fso2.FileExists(f) Then
                ToPythonW = f
                Exit Function
            End If
        End If
    End If
    ToPythonW = p
End Function

' 按优先级找可用的 Python
Function PickPython()
    Dim cands, anyPy, i, p
    cands = Array( _
        baseDir & "\.venv\Scripts\python.exe", _
        "python", _
        "python3", _
        "py -3" _
    )
    anyPy = ""

    For i = 0 To UBound(cands)
        p = cands(i)
        If InStr(p, "\") > 0 Then
            If fso.FileExists(p) Then
                If HasDeps(p) Then
                    PickPython = p
                    Exit Function
                End If
                If anyPy = "" Then anyPy = p
            End If
        Else
            If IsPythonCmd(p) Then
                If HasDeps(p) Then
                    PickPython = p
                    Exit Function
                End If
                If anyPy = "" Then anyPy = p
            End If
        End If
    Next

    PickPython = anyPy
End Function

Function IsPythonCmd(cmd)
    Dim exec, out
    On Error Resume Next
    Set exec = shell.Exec("cmd /c " & cmd & " --version 2>&1")
    If Err.Number <> 0 Then
        IsPythonCmd = False
        On Error GoTo 0
        Exit Function
    End If
    out = ""
    If Not exec.StdOut Is Nothing Then out = exec.StdOut.ReadAll
    IsPythonCmd = (InStr(out, "Python 3") > 0)
    On Error GoTo 0
End Function

' 检查关键依赖（含 PyQt6 窗口引擎）
Function HasDeps(p)
    Dim exec, out
    On Error Resume Next
    Set exec = shell.Exec("cmd /c " & p & " -c ""import fastapi,pandas,numpy,uvicorn;from PyQt6.QtWebEngineWidgets import QWebEngineView"" 2>&1")
    If Err.Number <> 0 Then
        HasDeps = False
        On Error GoTo 0
        Exit Function
    End If
    out = ""
    If Not exec.StdOut Is Nothing Then out = exec.StdOut.ReadAll
    HasDeps = (InStr(out, "Error") = 0 And InStr(out, "No module") = 0)
    On Error GoTo 0
End Function