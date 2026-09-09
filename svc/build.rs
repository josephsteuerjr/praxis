// Паспорт exe для службы. Оболочка и установщик собираются Tauri и несут
// VERSIONINFO сами, а служба собиралась голым cargo — и сборка поставки честно
// говорила «версии внутри нет, сверить нечем». Значит один из трёх бинарников
// мог уехать в поставку старым, и гард версий этого бы не заметил.
// FileVersion/ProductVersion tauri-winres берёт из CARGO_PKG_VERSION, то есть из
// того же svc/Cargo.toml, который сверяет build_dist.py.
fn main() {
    #[cfg(windows)]
    {
        let mut res = tauri_winres::WindowsResource::new();
        res.set("ProductName", "Helene");
        res.set("FileDescription", "Helene service supervisor");
        res.set("OriginalFilename", "helene-svc.exe");
        res.compile().expect("VERSIONINFO службы не собрался");
    }
}
