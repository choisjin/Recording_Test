; ReplayKit Inno Setup Script
; 컴파일: Inno Setup Compiler에서 이 파일을 열고 Ctrl+F9

#define MyAppName "ReplayKit"
#define MyAppVersion "1.0"
#define MyAppPublisher "ReplayKit"
#define MyAppExeName "ReplayKit.exe"

; dist/ReplayKit 경로 (build_dist.py 결과물)
#define DistDir "dist\ReplayKit"

[Setup]
AppId={{B8F2A3E1-4D5C-4F6A-9E7B-1C2D3E4F5A6B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ReplayKit_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
SetupIconFile=
; 설치 중 진행률 표시
ShowLanguageDialog=auto

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 메인 프로젝트 파일 (dist/ReplayKit 전체)
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Python 설치 파일 (임시 디렉토리에 추출)
Source: "{#DistDir}\python-3.10.4-amd64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
; Node.js 설치 파일 (임시 디렉토리에 추출)
Source: "{#DistDir}\node-v24.14.0-x64.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall
; VC++ Runtime (임시 디렉토리에 추출)
Source: "{#DistDir}\vcredist_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 생성"; GroupDescription: "추가 작업:"

[Code]
// Python 3.10 설치 여부 확인
function IsPythonInstalled: Boolean;
var
  ResultCode: Integer;
begin
  // py -3.10 런처로 확인
  Result := Exec('cmd.exe', '/c py -3.10 --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
  if not Result then
  begin
    // C:\Python310\python.exe 직접 확인
    Result := FileExists('C:\Python310\python.exe');
  end;
end;

// Node.js 설치 여부 확인
function IsNodeInstalled: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c where node.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

// VC++ Runtime 설치 여부 확인 (레지스트리)
function IsVCRedistInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64')
         or RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PythonInstaller: String;
  NodeInstaller: String;
  VCRedist: String;
begin
  if CurStep = ssPostInstall then
  begin
    // 0) VC++ Runtime 설치
    if not IsVCRedistInstalled then
    begin
      VCRedist := ExpandConstant('{tmp}\vcredist_x64.exe');
      if FileExists(VCRedist) then
      begin
        Log('VC++ Runtime 설치 시작...');
        Exec(VCRedist, '/install /quiet /norestart', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
        if ResultCode = 0 then
          Log('VC++ Runtime 설치 완료')
        else
          Log('VC++ Runtime 설치 결과: ' + IntToStr(ResultCode));
      end;
    end
    else
      Log('VC++ Runtime 이미 설치됨 - 건너뜀');

    // 1) Python 3.10 설치
    if not IsPythonInstalled then
    begin
      PythonInstaller := ExpandConstant('{tmp}\python-3.10.4-amd64.exe');
      if FileExists(PythonInstaller) then
      begin
        Log('Python 3.10 설치 시작...');
        // /quiet: 사일런트, TargetDir: C:\Python310, PrependPath: PATH 추가
        Exec(PythonInstaller,
             '/quiet InstallAllUsers=1 TargetDir=C:\Python310 PrependPath=1 Include_launcher=1',
             '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
        if ResultCode = 0 then
          Log('Python 3.10 설치 완료')
        else
          Log('Python 3.10 설치 실패: ' + IntToStr(ResultCode));
      end;
    end
    else
      Log('Python 3.10 이미 설치됨 - 건너뜀');

    // 2) Node.js 설치
    if not IsNodeInstalled then
    begin
      NodeInstaller := ExpandConstant('{tmp}\node-v24.14.0-x64.msi');
      if FileExists(NodeInstaller) then
      begin
        Log('Node.js 설치 시작...');
        Exec('msiexec.exe',
             '/i "' + NodeInstaller + '" /passive /norestart',
             '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
        if ResultCode = 0 then
          Log('Node.js 설치 완료')
        else
          Log('Node.js 설치 실패: ' + IntToStr(ResultCode));
      end;
    end
    else
      Log('Node.js 이미 설치됨 - 건너뜀');

    // 3) setup.bat 실행
    Log('setup.bat 실행 중...');
    Exec('cmd.exe', '/c "' + ExpandConstant('{app}\setup.bat') + '"',
         ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode);
  end;
end;

[Run]
; 설치 완료 후 ReplayKit 실행 옵션
Filename: "{app}\{#MyAppExeName}"; Description: "ReplayKit 실행"; Flags: nowait postinstall skipifsilent shellexec; Check: FileExists(ExpandConstant('{app}\{#MyAppExeName}'))
