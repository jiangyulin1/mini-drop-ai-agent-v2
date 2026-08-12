import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom deliberately does not implement pseudo-element styles. Ant Design's
// scrollbar measurement requests them and otherwise prints a misleading error
// even though the component and test both succeed.
const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element, pseudoElement) => (
  pseudoElement ? getComputedStyle(element) : getComputedStyle(element, pseudoElement)
);

afterEach(() => {
  cleanup();
});
