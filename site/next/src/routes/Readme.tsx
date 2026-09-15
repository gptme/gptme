import { eyebrow, wrap } from "../lib/cn";
import { links } from "../lib/links";
import { sanitizeReadmeHtml } from "../lib/readme-sanitize.ts";

export function Readme({ html }: { html: string }) {
  return (
    <div className={wrap}>
      <header className="flex flex-col gap-3 pb-8 pt-16 max-sm:pt-10">
        <p className={eyebrow}>README.md</p>
        <h1 className="m-0 font-mono text-[44px] font-semibold leading-[1.1] tracking-display text-heading max-sm:text-[34px]">
          gptme
        </h1>
        <p className="m-0 text-muted-text">
          Rendered from the repository README at build time. <a href={`${links.github}#readme`}>View on GitHub</a>
        </p>
      </header>
      <article
        className="prose prose-lg max-w-[860px] pb-22 [overflow-wrap:anywhere] max-md:pb-16 max-sm:prose-base"
        dangerouslySetInnerHTML={{ __html: sanitizeReadmeHtml(html) }}
      />
    </div>
  );
}
