fn main() {
    // Cargo's cached build script must not retain an older Windows resource
    // after the icon changes. The taskbar uses the PE icon, unlike our tray.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=icons/icon.png");
    tauri_build::try_build(tauri_build::Attributes::new().windows_attributes(
        tauri_build::WindowsAttributes::new().window_icon_path("icons/icon.ico"),
    ))
    .expect("Unable to embed the application resources");
}
