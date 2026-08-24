(() => {
  const versionNode = document.getElementById("app-version");
  if (!versionNode) return;

  fetch("api/health", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`health ${response.status}`);
      return response.json();
    })
    .then((health) => {
      versionNode.textContent = health.version ? `v${health.version}` : "";
    })
    .catch(() => {
      versionNode.textContent = "";
    });
})();
