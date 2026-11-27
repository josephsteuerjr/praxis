// Точка входа программы Праксис. Два режима:
//   без ключей            — окно (window.cjs);
//   --proxy-keeper        — хранитель прокси без окна (proxy-keeper.cjs): SSH-туннель до машины
//                          с tinyproxy, через который окно и тело Праксис ходят к её серверу
//                          без VPN. Обычно его держит задача «Praxis Proxy» при входе в Windows.
if (process.argv.includes("--proxy-keeper")) require("./proxy-keeper.cjs").run();
else require("./window.cjs");
