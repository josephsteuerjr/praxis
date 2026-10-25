//! Extract independent large members concurrently, retaining ZIP CRC checks.
use std::{collections::{HashMap,HashSet},fs::File,path::Path,sync::{atomic::{AtomicUsize,Ordering},Mutex}};

pub fn extract(archive:&Path,staging:&Path,progress:impl Fn(u64,u64,usize,usize)+Sync)->Result<(),String>{
    let mut reader=zip::ZipArchive::new(File::open(archive).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    let mut entries=Vec::new();let mut names=HashMap::new();let mut total=0;
    for index in 0..reader.len(){
        let entry=reader.by_index(index).map_err(|e|e.to_string())?;
        let name=entry.name();
        let path=entry.enclosed_name().ok_or("Invalid ZIP path")?.to_path_buf();
        if name.contains(['\\',':']) || !path.starts_with("Helene") || name.split('/').any(|part|part==".." || part.ends_with(['.',' '])) || entry.unix_mode().is_some_and(|m|m&0o170000==0o120000){return Err("Invalid ZIP member".into())}
        let key=name.trim_end_matches('/').to_lowercase();
        if names.insert(key,entry.is_dir()).is_some(){return Err("Duplicate ZIP member".into())}
        if !entry.is_dir(){total+=entry.size();entries.push((index,path,entry.size()));}
    }
    let files:HashSet<_>=names.iter().filter(|(_,directory)|!**directory).map(|(name,_)|name.as_str()).collect();
    for name in names.keys(){
        let mut parent=Path::new(name).parent();
        while let Some(path)=parent{if files.contains(path.to_string_lossy().replace('\\',"/").as_str()){return Err("ZIP file/directory collision".into())}parent=path.parent();}
    }
    std::fs::create_dir_all(staging).map_err(|e|e.to_string())?;
    let staging=staging.canonicalize().map_err(|e|e.to_string())?;
    let state=Mutex::new((0u64,0usize,std::time::Instant::now()));
    let report=|bytes|{let mut s=state.lock().unwrap();s.0+=bytes;s.1+=1;if s.2.elapsed().as_millis()>=100 || s.1==entries.len(){progress(s.0,total,s.1,entries.len());s.2=std::time::Instant::now();}};
    let unpack=|reader:&mut zip::ZipArchive<File>,index:usize,path:&Path|->Result<(),String>{
        let destination=staging.join(path);let parent=destination.parent().ok_or("No ZIP parent")?;
        std::fs::create_dir_all(parent).map_err(|e|e.to_string())?;
        if !parent.canonicalize().map_err(|e|e.to_string())?.starts_with(&staging){return Err("ZIP output escapes staging".into())}
        let temporary=parent.join(format!(".helene-{}-{index}.part",std::process::id()));
        let result=(||{
            let mut input=reader.by_index(index).map_err(|e|e.to_string())?;
            let mut output=std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|e|e.to_string())?;
            let bytes=std::io::copy(&mut input,&mut output).map_err(|e|e.to_string())?;drop(output);
            if destination.exists(){std::fs::remove_file(&destination).map_err(|e|e.to_string())?;}
            std::fs::rename(&temporary,&destination).map_err(|e|e.to_string())?;report(bytes);Ok(())
        })();let _=std::fs::remove_file(temporary);result
    };
    let large:Vec<_>=entries.iter().filter(|(_,_,size)|*size>=262144).collect();
    let next=AtomicUsize::new(0);
    let workers=std::thread::available_parallelism().map_or(1,|n|n.get()).min(12);
    std::thread::scope(|scope|{
        let mut threads=Vec::new();
        for _ in 0..workers.saturating_sub(1).min(large.len()){
            threads.push(scope.spawn(||->Result<(),String>{
                let mut reader=zip::ZipArchive::new(File::open(archive).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
                loop{let i=next.fetch_add(1,Ordering::Relaxed);if i>=large.len(){break}let (index,path,_)=large[i];unpack(&mut reader,*index,path)?;}Ok(())
            }));
        }
        let mut result=Ok(());
        for (index,path,size) in &entries{if workers==1 || *size<262144{if let Err(error)=unpack(&mut reader,*index,path){result=Err(error);break}}}
        for thread in threads{let done=thread.join().map_err(|_|"ZIP worker failed".to_string()).and_then(|r|r);if result.is_ok(){result=done;}}
        result
    })
}

#[cfg(test)]
mod tests{
    use super::*;use std::io::Write;
    #[test]
    fn refuses_aliases_before_writing_any_payload(){
        let dir=std::env::temp_dir().join(format!("helene-zip-{}",std::process::id()));std::fs::create_dir_all(&dir).unwrap();
        let archive=dir.join("aliases.zip");let mut zip=zip::ZipWriter::new(File::create(&archive).unwrap());
        for name in ["Helene/app/a.py","Helene/app/A.py"]{zip.start_file(name,zip::write::SimpleFileOptions::default()).unwrap();zip.write_all(b"content").unwrap();}zip.finish().unwrap();
        assert!(extract(&archive,&dir.join("out"),|_,_,_,_|{}).is_err());assert!(!dir.join("out").exists());std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn large_and_small_members_with_shared_stems_remain_exact(){
        let dir=std::env::temp_dir().join(format!("helene-zip-mixed-{}",std::process::id()));std::fs::create_dir_all(&dir).unwrap();
        let archive=dir.join("mixed.zip");let mut zip=zip::ZipWriter::new(File::create(&archive).unwrap());
        let entries=[("Helene/runtime/module.py",vec![b'a';1024*1024]),("Helene/runtime/module.dll",vec![b'b';1024*1024]),("Helene/app/start.py",b"print('ready')".to_vec())];
        for (name,bytes) in &entries{zip.start_file(*name,zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated)).unwrap();zip.write_all(bytes).unwrap();}zip.finish().unwrap();
        extract(&archive,&dir.join("out"),|_,_,_,_|{}).unwrap();
        for (name,bytes) in entries{assert_eq!(std::fs::read(dir.join("out").join(name)).unwrap(),bytes);}
        std::fs::remove_dir_all(dir).unwrap();
    }
}
