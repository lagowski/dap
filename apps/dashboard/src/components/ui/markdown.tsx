"use client";

import ReactMarkdown, { type Components } from "react-markdown";

/**
 * Render LLM-generated prose as Markdown (#689): the config assistant chat, the
 * AI error explanation, agent docstrings. react-markdown renders no raw HTML
 * and sanitizes dangerous URL protocols, so it's safe for model output. Styling
 * is a controlled ``components`` map (not the typography plugin) so it stays
 * tight inside narrow panels.
 */
const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="mt-2 mb-1 text-sm font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-2 mb-1 text-sm font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-2 mb-1 text-sm font-semibold">{children}</h3>,
  p: ({ children }) => <p className="my-1.5 leading-snug">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-4">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-4">{children}</ol>,
  li: ({ children }) => <li className="leading-snug">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 underline hover:no-underline dark:text-blue-400"
    >
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    // Block code carries a ``language-*`` class; inline code does not.
    const isBlock = typeof className === "string" && className.startsWith("language-");
    if (isBlock) {
      return (
        <code className="block overflow-x-auto rounded bg-black/5 p-2 font-mono text-xs dark:bg-white/10">
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/15">
        {children}
      </code>
    );
  },
  pre: ({ children }) => <pre className="my-1.5">{children}</pre>,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm">
      <ReactMarkdown components={COMPONENTS}>{children}</ReactMarkdown>
    </div>
  );
}
