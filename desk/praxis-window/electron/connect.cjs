// Страница входа: подключается сохранённым ключом сама; ключ спрашивает, только когда
// сервер его не принимает. Когда связи нет — говорит словами и пробует снова сама.
const form = document.querySelector("form");
const input = document.getElementById("key");
const statusLine = document.getElementById("status");
const retry = document.getElementById("retry");
let timer = 0;

async function enter(key) {
  clearTimeout(timer);
  form.hidden = true;
  retry.hidden = true;
  statusLine.textContent = "Подключаюсь…";
  const answer = await window.praxisWindow.connect(key);
  if (!answer || answer.ok || answer.reason === "ignored") return;
  if (answer.reason === "offline") {
    statusLine.textContent = `${answer.message} Как только связь появится, окно откроется само.`;
    retry.hidden = false;
    timer = setTimeout(() => void enter(""), 5000);
    return;
  }
  statusLine.textContent = answer.message;
  form.hidden = false;
  input.focus();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const key = input.value;
  input.value = "";
  void enter(key);
});
retry.addEventListener("click", () => void enter(""));
void enter("");
