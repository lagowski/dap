import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMarkdown } from "./chat-markdown";

describe("ChatMarkdown (#689)", () => {
  it("renders headings, bold, and inline code as elements (not raw markdown)", () => {
    const { container } = render(
      <ChatMarkdown>{"# Title\n\nUse **api-call** with `model_id`."}</ChatMarkdown>,
    );
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("strong")?.textContent).toBe("api-call");
    expect(container.querySelector("code")?.textContent).toBe("model_id");
    // The raw markers must not leak into the rendered text.
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("# Title");
  });

  it("renders lists", () => {
    const { container } = render(<ChatMarkdown>{"- one\n- two"}</ChatMarkdown>);
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders links safely with target+rel", () => {
    render(<ChatMarkdown>{"[docs](/docs/runtimes.md)"}</ChatMarkdown>);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "/docs/runtimes.md");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not execute raw HTML in the markdown", () => {
    const { container } = render(
      <ChatMarkdown>{"text <img src=x onerror=alert(1)> more"}</ChatMarkdown>,
    );
    // react-markdown ignores raw HTML by default — no img element is created.
    expect(container.querySelector("img")).toBeNull();
  });
});
