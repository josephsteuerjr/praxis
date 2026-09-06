// Операция со службой под правами администратора — один рецепт на окно и
// установщик.
//
// Рецепт установщика: скрипт во временной папке, запуск через UAC с
// `-Wait -PassThru` и ЧТЕНИЕМ кода возврата, потом опрос SCM. У окна до
// 07.09 было иначе: `Start-Process -Verb RunAs` без ожидания, результат не
// читался, и «запрошено» означало «сделано» — отказ от прав в UAC выглядел
// успехом (ревью 06.09, §4 п. 14, решение 7). Теперь окно ходит этим же
// путём: файл включается через `include!` в `shell/src/main.rs` и
// `setup/src/install.rs`; для сборки команды нужен `common/ps.rs`.

/// Скрипт-обёртка. Коды выхода: 0 — сделано, 1 — служба осталась, 2 — нет
/// скрипта поставки. Автоперезапуск снимается ДО остановки: иначе SCM поднимет
/// службу через 5 секунд (install-service.ps1 прописывает restart/5000) прямо
/// посреди снятия.
const SERVICE_OP_PS1: &str = r#"# Служебные операции Hélène под правами администратора.
# Коды выхода: 0 — сделано, 1 — служба осталась, 2 — нет скрипта поставки.
param([string]$Op, [string]$Name, [string]$Script)
$ErrorActionPreference = 'Continue'

function Gone { & sc.exe query $Name *> $null; return ($LASTEXITCODE -ne 0) }

if ($Op -eq 'install') {
  if (-not (Test-Path $Script)) { exit 2 }
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script
  exit $LASTEXITCODE
}

# Автоперезапуск снимаем ДО остановки: иначе SCM поднимет службу через 5 секунд
# (install-service.ps1 прописывает restart/5000) прямо посреди снятия.
& sc.exe failure $Name reset= 0 actions= "" *> $null
& sc.exe config $Name start= disabled *> $null

if ($Op -eq 'stop') {
  & sc.exe stop $Name *> $null
  for ($i = 0; $i -lt 30; $i++) {
    if (Gone) { exit 0 }
    $q = & sc.exe query $Name 2>$null
    if ($q -match 'STOPPED') { exit 0 }
    Start-Sleep -Milliseconds 400
  }
  exit 1
}

# uninstall
if ($Script -and (Test-Path $Script)) {
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script *> $null
}
for ($i = 0; $i -lt 30; $i++) {
  if (Gone) { exit 0 }
  & sc.exe stop $Name *> $null
  & sc.exe delete $Name *> $null
  Start-Sleep -Milliseconds 400
}
exit 1
"#;

/// Код выхода обёртки, которым отвечает внешний PowerShell, когда UAC
/// отклонён или Start-Process не состоялся.
const SERVICE_OP_DENIED: i32 = 5;

/// Команда для внешнего `powershell -Command …`: поднятый вызов обёртки с
/// ожиданием и кодом возврата. Аргументы одной строкой и каждый в двойных
/// кавычках: Start-Process с массивом склеивает элементы пробелом и сам
/// ничего не экранирует, поэтому путь с пробелом (C:\Users\John Smith\…)
/// разъехался бы на два аргумента. Вся строка — в одинарных кавычках
/// PowerShell, значит апостроф удваивается (`ps_escape`).
fn service_op_command(wrapper: &std::path::Path, op: &str, name: &str, script: Option<&std::path::Path>) -> String {
    let script_arg = script.map(|p| p.display().to_string()).unwrap_or_default();
    format!(
        "try {{ $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"{}\" -Op \"{}\" -Name \"{}\" -Script \"{}\"'; exit $p.ExitCode }} catch {{ exit {SERVICE_OP_DENIED} }}",
        ps_escape(&wrapper.display().to_string()),
        ps_escape(op),
        ps_escape(name),
        ps_escape(&script_arg),
    )
}

/// Приговор по коду выхода — словами владельцу.
fn service_op_verdict(code: Option<i32>) -> Result<(), String> {
    match code {
        Some(0) => Ok(()),
        Some(2) => Err("в этой поставке нет скрипта службы".into()),
        Some(SERVICE_OP_DENIED) => Err("права администратора не были даны".into()),
        Some(code) => Err(format!("служба не поддалась (код {code})")),
        None => Err("вызов службы прерван".into()),
    }
}

#[cfg(test)]
mod service_op_tests {
    use super::*;

    #[test]
    fn command_waits_and_reads_exit_code() {
        let cmd = service_op_command(
            std::path::Path::new(r"C:\Users\O'Brien\Temp\helene-service-op.ps1"),
            "install",
            "Helene",
            Some(std::path::Path::new(r"C:\Program Files\Helene\install-service.ps1")),
        );
        assert!(cmd.contains("-Wait -PassThru"), "{cmd}");
        assert!(cmd.contains("exit $p.ExitCode"), "{cmd}");
        assert!(cmd.contains(r"O''Brien"), "апостроф в пути должен удвоиться: {cmd}");
        assert!(cmd.contains("-Script \"C:\\Program Files\\Helene\\install-service.ps1\""), "{cmd}");
        assert!(cmd.ends_with("catch { exit 5 }"), "{cmd}");
    }

    #[test]
    fn verdicts_name_the_reason() {
        assert!(service_op_verdict(Some(0)).is_ok());
        assert!(service_op_verdict(Some(5)).unwrap_err().contains("администратора"));
        assert!(service_op_verdict(Some(2)).unwrap_err().contains("нет скрипта"));
        assert!(service_op_verdict(Some(1)).unwrap_err().contains("код 1"));
        assert!(service_op_verdict(None).unwrap_err().contains("прерван"));
    }
}
