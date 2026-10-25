#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
//! Installation-only window. Daily Hélène never depends on this binary.
//! Build via build-migration.ps1: Tauri codegen reads the directory's default
//! config even with generate_context!("migration.conf.json"); TAURI_CONFIG
//! must carry the migration config to both the build helper and codegen.
use std::{io::{BufRead, BufReader}, path::PathBuf, process::{Command, Stdio}, sync::atomic::{AtomicBool, Ordering}};
use tauri::Emitter;
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!("Migration release requires --features custom-protocol");
static RUNNING: AtomicBool = AtomicBool::new(false);
fn release()->String {format!("https://github.com/josephsteuerjr/praxis/releases/download/v{}",env!("CARGO_PKG_VERSION"))}
fn main_asset()->String {format!("Helene-{}.zip",env!("CARGO_PKG_VERSION"))}
fn package_present(root:&std::path::Path)->bool {
    root.join("runtime/python.exe").is_file() && root.join("app/python_migrate.py").is_file()
}
fn cached_archive(app:&tauri::AppHandle,downloads:&std::path::Path,asset:&str,existing:Option<&std::path::Path>)->Result<PathBuf,String>{
    use sha2::{Digest,Sha256};
    use std::io::Read;
    let offline=argument("--offline-dir").map(PathBuf::from);
    let sum=if let Some(ref directory)=offline {
        std::fs::read_to_string(directory.join(format!("{asset}.sha256"))).map_err(|e|format!("Offline checksum {asset}: {e}"))?
    } else {ureq::get(&format!("{}/{asset}.sha256",release())).call().map_err(|e|e.to_string())?.into_string().map_err(|e|e.to_string())?};
    let expected=sum.split_whitespace().next().ok_or("Missing archive hash")?;
    if expected.len()!=64 || !expected.bytes().all(|b|b.is_ascii_hexdigit()){return Err("Invalid archive SHA-256".into())}
    let archive=existing.map(std::path::Path::to_path_buf).unwrap_or_else(||offline.as_ref().map_or_else(||downloads.join(asset),|directory|directory.join(asset)));
    let matches=|path:&std::path::Path|->Result<bool,String>{
        if !path.is_file(){return Ok(false)}
        let mut file=std::fs::File::open(path).map_err(|e|e.to_string())?;
        let mut hash=Sha256::new();let mut buffer=[0u8;65536];
        loop {let n=file.read(&mut buffer).map_err(|e|e.to_string())?;if n==0{break}hash.update(&buffer[..n]);}
        Ok(format!("{:x}",hash.finalize())==expected)
    };
    if matches(&archive)? {return Ok(archive)}
    if offline.is_some(){return Err(format!("Offline archive missing or changed: {}",archive.display()))}
    if existing.is_some(){return Err(format!("Source ZIP missing or SHA-256 mismatch: {}. Download the updated release again.",archive.display()))}
    let part=downloads.join(format!("{asset}.part"));
    let response=ureq::get(&format!("{}/{asset}",release())).call().map_err(|e|format!("Download {asset}: {e}"))?;
    let mut file=std::fs::File::create(&part).map_err(|e|e.to_string())?;
    let total=response.header("Content-Length").and_then(|s|s.parse::<u64>().ok());
    let mut reader=response.into_reader();let mut bytes=0u64;let mut buffer=[0u8;262144];
    let mut reported=std::time::Instant::now();
    loop {let n=reader.read(&mut buffer).map_err(|e|format!("Download {asset}: {e}"))?;if n==0{break}
        std::io::Write::write_all(&mut file,&buffer[..n]).map_err(|e|e.to_string())?;bytes+=n as u64;
        if reported.elapsed().as_millis()>=200 {let _=app.emit("migration-progress",serde_json::json!({"stage":"downloading","asset":asset,"bytes":bytes,"total_bytes":total}));reported=std::time::Instant::now();}
    }
    let _=app.emit("migration-progress",serde_json::json!({"stage":"downloading","asset":asset,"bytes":bytes,"total_bytes":total}));drop(file);
    if !matches(&part)?{return Err(format!("Archive hash mismatch: {asset}"))}
    if archive.exists(){let previous=archive.with_file_name(format!("{asset}.previous-{}",std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));std::fs::rename(&archive,previous).map_err(|e|e.to_string())?;}
    std::fs::rename(&part,&archive).map_err(|e|e.to_string())?;Ok(archive)
}
fn verify_package(archive:&std::path::Path,root:&std::path::Path)->Result<(),String>{
    use sha2::{Digest,Sha256};use std::io::Read;
    fn hash(mut file:impl Read)->Result<Vec<u8>,String>{let mut h=Sha256::new();let mut b=[0u8;65536];loop{let n=file.read(&mut b).map_err(|e|e.to_string())?;if n==0{break}h.update(&b[..n]);}Ok(h.finalize().to_vec())}
    let mut zip=zip::ZipArchive::new(std::fs::File::open(archive).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    for i in 0..zip.len(){let mut entry=zip.by_index(i).map_err(|e|e.to_string())?;if entry.is_dir(){continue}
        let rel=entry.enclosed_name().ok_or("Invalid ZIP path")?;
        let rel=rel.strip_prefix("Helene").map_err(|_|"Unexpected ZIP root")?;
        let file=std::fs::File::open(root.join(rel)).map_err(|e|format!("Package {}: {e}",rel.display()))?;
        if hash(&mut entry)?!=hash(file)?{return Err(format!("Unpacked package changed: {}",rel.display()))}
    }Ok(())
}
fn extract(app:&tauri::AppHandle,archive:&std::path::Path,staging:&std::path::Path,asset:&str)->Result<(),String>{
    let mut zip=zip::ZipArchive::new(std::fs::File::open(archive).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    let total=(0..zip.len()).try_fold(0u64,|sum,i|zip.by_index(i).map(|e|sum.saturating_add(e.size()))).map_err(|e|e.to_string())?;
    let count=zip.len();let mut done=0u64;let mut reported=std::time::Instant::now();
    for i in 0..count {let mut entry=zip.by_index(i).map_err(|e|e.to_string())?;
        let relative=entry.enclosed_name().ok_or("Invalid archive path")?.to_path_buf();
        if !relative.starts_with("Helene"){return Err("Unexpected archive root".into())}
        if entry.unix_mode().is_some_and(|m|m&0o170000==0o120000){return Err("Archive symlink is unsupported".into())}
        let destination=staging.join(&relative);
        if entry.is_dir(){std::fs::create_dir_all(&destination).map_err(|e|e.to_string())?;continue}
        std::fs::create_dir_all(destination.parent().ok_or("No destination parent")?).map_err(|e|e.to_string())?;
        let temporary=destination.with_extension("migration-part");
        let mut output=std::fs::File::create(&temporary).map_err(|e|e.to_string())?;
        done+=std::io::copy(&mut entry,&mut output).map_err(|e|format!("Extract {asset}: {e}"))?;drop(output);
        if destination.exists(){std::fs::remove_file(&destination).map_err(|e|e.to_string())?;}
        std::fs::rename(&temporary,&destination).map_err(|e|e.to_string())?;
        if reported.elapsed().as_millis()>=200 || i+1==count {let _=app.emit("migration-progress",serde_json::json!({"stage":"unpacking","asset":asset,"bytes":done,"total_bytes":total,"files":i+1,"total_files":count}));reported=std::time::Instant::now();}
    }Ok(())
}
fn package(app:&tauri::AppHandle, extensions:&[String]) -> Result<PathBuf,String> {
    let downloads=PathBuf::from(std::env::var_os("USERPROFILE").ok_or("USERPROFILE unavailable")?).join("Downloads").join("Helene-downloads").join(env!("CARGO_PKG_VERSION"));
    std::fs::create_dir_all(&downloads).map_err(|e|e.to_string())?;
    let beside=std::env::current_exe().map_err(|e|e.to_string())?.parent().ok_or("No setup directory")?.to_path_buf();
    // The old updater already unpacked the main ZIP. Never download it again.
    let local=package_present(&beside);
    if local {
        let source=argument("--archive").map(PathBuf::from).unwrap_or_else(||{let mut name=beside.parent().unwrap().as_os_str().to_os_string();name.push(".zip");PathBuf::from(name)});
        cached_archive(app,&downloads,&main_asset(),Some(&source))?;
        verify_package(&source,&beside)?;
    }
    let staging=if local {beside.parent().ok_or("No package parent")?.to_path_buf()} else {
        let path=downloads.join(format!("Helene-migration-{}",env!("CARGO_PKG_VERSION")));std::fs::create_dir_all(&path).map_err(|e|e.to_string())?;path
    };
    let root=if local {beside} else {staging.join("Helene")};
    let main=main_asset();
    if !local {
        let archive=cached_archive(app,&downloads,&main,None)?;
        if verify_package(&archive,&root).is_err(){extract(app,&archive,&staging,&main)?;}
    }
    if !package_present(&root){return Err("Incomplete Python package".into())}
    // Verified incoming Python owns the shared URL/SHA parser. No second Rust
    // manifest dialect and no same-tag guess for optional heavyweight archives.
    let mut command=Command::new(root.join("runtime/python.exe"));
    command.args(["-s","-m","localharness.component_downloads","--prepare"])
        .arg(&root).args(["--extensions",&extensions.join(",")])
        .current_dir(&root).stdout(Stdio::piped()).stderr(Stdio::piped());
    if let Some(directory)=argument("--offline-dir"){command.args(["--offline",&directory]);}
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
    let mut child=command.spawn().map_err(|e|e.to_string())?;
    let stdout=child.stdout.take().ok_or("Component progress unavailable")?;
    let stderr=child.stderr.take().ok_or("Component errors unavailable")?;
    let errors=std::thread::spawn(move||{use std::io::Read;let mut text=String::new();let _=stderr.take(65536).read_to_string(&mut text);text});
    for line in BufReader::new(stdout).lines(){
        let line=line.map_err(|e|e.to_string())?;
        if let Ok(value)=serde_json::from_str::<serde_json::Value>(&line){let _=app.emit("migration-progress",value);}
    }
    let status=child.wait().map_err(|e|e.to_string())?;
    let error=errors.join().map_err(|_|"Component error reader failed")?;
    if !status.success(){return Err(format!("Optional components: {error}"))}
    Ok(root)
}
fn argument(key:&str)->Option<String>{let a:Vec<_>=std::env::args().collect();a.windows(2).find(|w|w[0]==key).map(|w|w[1].clone())}
fn selected_extensions(old:&std::path::Path,mode:&str)->Vec<String>{
    if mode=="fresh" {return vec!["xtts".into()]}
    let mut selected=Vec::new();
    for (name,path) in [("xtts","components/xtts"),("xtts","voice-xtts"),("toolchain","components/toolchain"),("whisper","voice/site-packages"),("whisper","runtime/faster_whisper"),("whisper","runtime/Lib/site-packages/faster_whisper")] {
        if old.join(path).is_dir() && !selected.iter().any(|value|value==name){selected.push(name.to_owned());}
    }
    selected
}
#[tauri::command]
async fn migration_pick_path(current:String)->Result<Option<String>,String>{
    #[cfg(windows)] {
        tauri::async_runtime::spawn_blocking(move||{
            use std::os::windows::process::CommandExt;
            let script="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.FolderBrowserDialog;$d.Description='Hélène';$d.ShowNewFolderButton=$true;if(Test-Path -LiteralPath $env:HELENE_PICK_START){$d.SelectedPath=$env:HELENE_PICK_START};try{if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Write($d.SelectedPath)}}finally{$d.Dispose()}";
            let output=Command::new("powershell.exe").args(["-NoProfile","-STA","-Command",script]).env("HELENE_PICK_START",current).creation_flags(0x08000000).output().map_err(|e|e.to_string())?;
            if !output.status.success(){return Err("Windows folder selection failed".into())}
            let text=String::from_utf8(output.stdout).map_err(|e|e.to_string())?;
            Ok(if text.trim().is_empty(){None}else{Some(text.trim().to_owned())})
        }).await.map_err(|e|e.to_string())?
    }
    #[cfg(not(windows))] {let _=current;Err("This installer is for Windows".into())}
}
#[tauri::command]
fn migration_defaults()->Result<serde_json::Value,String>{
    let local=std::env::var_os("LOCALAPPDATA").map(PathBuf::from).ok_or("LOCALAPPDATA unavailable")?;
    let old=argument("--dir").map(PathBuf::from).unwrap_or_else(||PathBuf::from(std::env::var_os("ProgramFiles").unwrap_or("C:/Program Files".into())).join("Helene"));
    let mode=argument("--mode").unwrap_or_else(||if old.join("python-app.json").is_file(){"update".into()}else if old.join("helene.json").is_file(){"migrate".into()}else{"fresh".into()});
    let target=argument("--new-dir").map(PathBuf::from).unwrap_or_else(||if mode=="update"{old.clone()}else{local.join("Helene")});
    Ok(serde_json::json!({"old":old,"target":target,"default_target":local.join("Helene"),"mode":mode,"offline":argument("--offline-dir").is_some(),"resume":argument("--resume-copy").and_then(|p|PathBuf::from(p).parent().map(|p|p.to_string_lossy().to_string())),"version":env!("CARGO_PKG_VERSION"),"extensions":argument("--extensions").map(|s|s.split(',').filter(|s|!s.is_empty()).map(str::to_owned).collect::<Vec<_>>()).unwrap_or_else(||selected_extensions(&old,&mode))}))
}
#[cfg(windows)]
fn elevated()->bool { unsafe { windows_sys::Win32::UI::Shell::IsUserAnAdmin()!=0 } }
#[cfg(not(windows))]
fn elevated()->bool {true}
fn migration_command(root:&std::path::Path,old:&str,target:&str,extensions:&[String],mode:&str,resume:Option<&str>)->Command {
    let mut command=Command::new(root.join("runtime/python.exe"));
    command.args(["-s"]).arg(root.join("app/python_migrate.py"))
        .args(["--update","--confirmed","--stop-service","--dir",old,"--new-dir",target,"--extensions",&extensions.join(",")])
        .current_dir(root).stdout(Stdio::piped()).stderr(Stdio::null());
    if mode=="fresh"{command.arg("--fresh");}
    if mode=="update"{command.arg("--in-place");}
    if let Some(directory)=resume{command.arg("--resume-copy").arg(PathBuf::from(directory).join("python-migration.json"));}
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
    command
}
#[cfg(windows)]
fn elevate(old:&str,target:&str,extensions:&[String],mode:&str,resume:Option<&str>)->Result<(),String>{
    use std::os::windows::ffi::OsStrExt;
    fn wide(s:&std::ffi::OsStr)->Vec<u16>{s.encode_wide().chain(Some(0)).collect()}
    // Windows paths cannot contain quotes; reject rather than alter arguments.
    if old.contains('"')||target.contains('"'){return Err("Invalid directory".into())}
    let exe=std::env::current_exe().map_err(|e|e.to_string())?;
    let file=wide(exe.as_os_str());let verb=wide("runas".as_ref());
    let offline=if let Some(path)=argument("--offline-dir") {
        if path.contains('"'){return Err("Invalid offline directory".into())}
        format!(" --offline-dir \"{}\"",path.trim_end_matches('\\'))
    }else{String::new()};
    let prior=if let Some(directory)=resume{if directory.contains('"'){return Err("Invalid resume directory".into())}format!(" --resume-copy \"{}\"",PathBuf::from(directory).join("python-migration.json").display())}else{String::new()};
    let args=wide(format!("--update --dir \"{}\" --new-dir \"{}\" --extensions \"{}\" --mode {}{}{}",old.trim_end_matches('\\'),target.trim_end_matches('\\'),extensions.join(","),mode,offline,prior).as_ref());
    let result=unsafe{windows_sys::Win32::UI::Shell::ShellExecuteW(std::ptr::null_mut(),verb.as_ptr(),file.as_ptr(),args.as_ptr(),std::ptr::null(),1)};
    if result as isize<=32 {return Err("Windows elevation cancelled or refused".into())} Ok(())
}
#[tauri::command]
async fn migration_run(app:tauri::AppHandle,old:String,target:String,extensions:Option<Vec<String>>,mode:Option<String>,resume:Option<String>)->Result<serde_json::Value,String>{
    if RUNNING.swap(true,Ordering::SeqCst){return Err("Migration already started; inspect the retained result".into())}
    let old_path=PathBuf::from(&old);
    let mode=mode.unwrap_or_else(||"migrate".into());
    let resume=resume.filter(|value|!value.trim().is_empty());
    if let Some(ref directory)=resume{if mode!="migrate"||!PathBuf::from(directory).join("python-migration.json").is_file(){RUNNING.store(false,Ordering::SeqCst);return Err("Selected resume directory has no migration journal".into())}}
    if !matches!(mode.as_str(),"fresh"|"migrate"|"update"){RUNNING.store(false,Ordering::SeqCst);return Err("Unknown installation mode".into())}
    if mode!="fresh" && !old_path.join("helene.json").is_file(){RUNNING.store(false,Ordering::SeqCst);return Err("No Hélène configuration in selected installation".into())}
    let extensions=extensions.unwrap_or_else(||selected_extensions(&old_path,&mode));
    if extensions.iter().any(|s|!matches!(s.as_str(),"xtts"|"toolchain"|"whisper")) {
        RUNNING.store(false,Ordering::SeqCst);return Err("Unknown extension selection".into());
    }
    #[cfg(windows)]
    if mode!="fresh" && !elevated() && old_path.starts_with(PathBuf::from(std::env::var_os("ProgramFiles").unwrap_or_default())) {
        let result=elevate(&old,&target,&extensions,&mode,resume.as_deref());RUNNING.store(false,Ordering::SeqCst);result?;return Ok(serde_json::json!({"elevated":true}));
    }
    let result=tauri::async_runtime::spawn_blocking(move||{
        let root=match package(&app,&extensions){Ok(root)=>root,Err(e)=>{RUNNING.store(false,Ordering::SeqCst);return Err(format!("Preparation: {e}"))}};
        let mut command=migration_command(&root,&old,&target,&extensions,&mode,resume.as_deref());
        let mut child=command.spawn().map_err(|e|e.to_string())?;
        let output=child.stdout.take().ok_or("Migration output unavailable")?;
        let mut last=None;
        for line in BufReader::new(output).lines(){
            let line=line.map_err(|e|e.to_string())?;
            if let Ok(value)=serde_json::from_str::<serde_json::Value>(&line){let _=app.emit("migration-progress",&value);last=Some(value);}
        }
        let status=child.wait().map_err(|e|e.to_string())?;
        if !status.success(){return Err(last.and_then(|v|v.get("error").and_then(|e|e.as_str()).map(str::to_owned)).unwrap_or("Migration interrupted; inspect python-migration.json".into()))}
        let note=std::fs::read_to_string(PathBuf::from(&target).join("python-migration.json")).map_err(|e|e.to_string())?;
        let note:serde_json::Value=serde_json::from_str(&note).map_err(|e|e.to_string())?;
        // The migrated application executes from target, never the download.
        if note.get("state").and_then(|s|s.as_str())==Some("completed") {
            if let Some(staging)=root.parent().filter(|p|p.file_name().is_some_and(|n|n.to_string_lossy().starts_with("Helene-migration-"))) {
                // Verified input archives remain reusable. Only this wizard's
                // extracted temporary program is retired, never the user's ZIP.
                // Windows may briefly retain the exited Python executable handle.
                for _ in 0..20 {if std::fs::remove_dir_all(staging).is_ok(){break} std::thread::sleep(std::time::Duration::from_millis(250));}
            }
        }
        Ok(note)
    }).await.map_err(|e|e.to_string());
    RUNNING.store(false,Ordering::SeqCst);
    result?
}
#[cfg(test)]
mod command_tests {
    use super::*;
    #[test]
    fn extension_defaults_preserve_existing_choices_instead_of_fresh_defaults() {
        let home=std::env::temp_dir().join(format!("helene-defaults-{}",std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        assert!(selected_extensions(&home,"update").is_empty());
        assert_eq!(selected_extensions(&home,"fresh"),vec!["xtts"]);
        std::fs::create_dir_all(home.join("voice-xtts")).unwrap();
        assert_eq!(selected_extensions(&home,"migrate"),vec!["xtts"]);
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn completion_marker_cannot_hide_missing_or_changed_component_bytes() {
        use std::io::Write;
        let home=std::env::temp_dir().join(format!("helene-component-test-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(home.join("root/components/toolchain")).unwrap();
        let archive=home.join("component.zip");
        let mut zip=zip::ZipWriter::new(std::fs::File::create(&archive).unwrap());
        zip.start_file("Helene/components/toolchain/compiler",zip::write::SimpleFileOptions::default()).unwrap();
        zip.write_all(b"verified-content").unwrap();zip.finish().unwrap();
        let root=home.join("root");let file=root.join("components/toolchain/compiler");
        std::fs::write(root.join("components/toolchain/.migration-complete"),"1.9.1").unwrap();
        assert!(verify_package(&archive,&root).is_err());
        std::fs::write(&file,b"changed-content").unwrap();assert!(verify_package(&archive,&root).is_err());
        std::fs::write(&file,b"verified-content").unwrap();assert!(verify_package(&archive,&root).is_ok());
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn component_selection_reaches_the_actual_python_child_argv() {
        for selected in [vec![],vec!["xtts".to_string()],vec!["toolchain".to_string(),"whisper".to_string()]] {
            let command=migration_command(std::path::Path::new("kit"),"old","new",&selected,"migrate",None);
            let args:Vec<_>=command.get_args().map(|s|s.to_string_lossy().to_string()).collect();
            let at=args.iter().position(|s|s=="--extensions").unwrap();
            assert_eq!(args[at+1],selected.join(","));
            assert!(args.windows(2).any(|a|a==["--new-dir","new"]));
        }
        for (mode,flag) in [("fresh","--fresh"),("update","--in-place")] {
            let command=migration_command(std::path::Path::new("kit"),"old","new",&[],mode,None);
            assert!(command.get_args().any(|argument|argument==flag));
        }
    }
}
fn main(){
    tauri::Builder::default().invoke_handler(tauri::generate_handler![migration_defaults,migration_run,migration_pick_path])
    .setup(|app|{
        // Monitor work areas are physical pixels; Tauri sizes below are logical.
        // Leave room for the native title bar/borders and taskbar at 150–200% DPI.
        let (width,height)=app.primary_monitor()?.map(|monitor|{
            let area=monitor.work_area();let scale=monitor.scale_factor();
            ((area.size.width as f64/scale-32.0).clamp(1.0,660.0),
             (area.size.height as f64/scale-80.0).clamp(1.0,660.0))
        }).unwrap_or((600.0,480.0));
        tauri::WebviewWindowBuilder::new(app,"main",tauri::WebviewUrl::App("index.html".into()))
            .title("Hélène · переход на Python").inner_size(width,height)
            .min_inner_size(width.min(360.0),height.min(320.0)).center().resizable(true).build()?;Ok(())})
    .run(tauri::generate_context!("migration.conf.json")).expect("Migration window failed");
}
