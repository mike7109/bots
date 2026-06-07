import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Этап 6 (паритет): markdown-рендер описания и комментов. react-markdown НЕ
// использует dangerouslySetInnerHTML (строит React-дерево из AST), поэтому
// XSS-безопасен без отдельного DOMPurify — raw-HTML внутри markdown не
// исполняется. remark-gfm добавляет таблицы, списки задач, ~~strikethrough~~,
// автоссылки — как в GitLab.
//
// Ссылки открываем в новой вкладке (карточка остаётся), с rel=noreferrer.
export const Markdown = memo(({ source }: { source: string }) => {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer" />
          ),
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
});
