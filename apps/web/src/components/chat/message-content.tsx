"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

import { cn } from "@/lib/utils";

// Many models (Qwen, Llama, GPT) emit math wrapped in `\(...\)` or `\[...\]`
// instead of the `$...$` / `$$...$$` syntax remark-math understands. Normalize
// before parsing so the math actually renders via KaTeX.
function normalizeMathDelimiters(input: string): string {
  return input
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, body) => `\n$$${body.trim()}$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, body) => `$${body.trim()}$`);
}

export function MessageContent({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  const normalized = normalizeMathDelimiters(content);
  return (
    <div
      className={cn(
        "prose-chat text-[15px] leading-7 text-foreground",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noreferrer noopener"
              className="font-medium text-primary underline underline-offset-2 hover:text-primary/80"
            />
          ),
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
