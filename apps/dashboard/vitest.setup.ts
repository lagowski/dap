/**
 * Vitest setup for the dashboard test suite (audit D5 infra).
 *
 * Runs once before each test file. Imports
 * ``@testing-library/jest-dom`` to extend Vitest's expect with DOM
 * matchers (``toBeInTheDocument``, ``toBeDisabled``, etc.) and
 * registers a global ``afterEach`` that unmounts React trees so DOM
 * state from one test never bleeds into the next.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
