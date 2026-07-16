"use strict";

for (const tabList of document.querySelectorAll('[role="tablist"]')) {
  const tabs = Array.from(tabList.querySelectorAll('[role="tab"]'));
  const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));

  if (tabs.length === 0 || panels.some((panel) => panel === null)) {
    continue;
  }

  const activate = (nextIndex, moveFocus) => {
    tabs.forEach((tab, index) => {
      const selected = index === nextIndex;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[index].hidden = !selected;
    });

    if (moveFocus) {
      tabs[nextIndex].focus();
    }
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(index, false));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = index;

      if (event.key === "ArrowRight") {
        nextIndex = (index + 1) % tabs.length;
      } else if (event.key === "ArrowLeft") {
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabs.length - 1;
      } else {
        return;
      }

      event.preventDefault();
      activate(nextIndex, true);
    });
  });

  const selectedIndex = Math.max(
    0,
    tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true"),
  );
  activate(selectedIndex, false);
}
