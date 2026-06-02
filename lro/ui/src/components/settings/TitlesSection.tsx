import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tag, Ban } from "lucide-react";
import type { UseFormRegisterReturn } from "react-hook-form";

/**
 * Job-title settings — two comma-separated textareas fed into the
 * ingest + match stages:
 *
 *   - role_keywords:          titles to KEEP. Substring-matched, so
 *     "product manager" alone catches senior/staff/principal/etc.
 *   - blocked_title_keywords: titles to DROP. Whole-word matched, so
 *     "engineer" catches "Software Engineer" but not "Engineering". A
 *     hit here is skipped at scrape time, hidden in the Matches view,
 *     AND penalised by the match score.
 *
 * Both textareas are registered with react-hook-form via the parent,
 * keeping form state (isDirty / isSubmitting) out of this component.
 */
interface Props {
  roleRegister: UseFormRegisterReturn;
  blockedRegister: UseFormRegisterReturn;
}

export function TitlesSection({ roleRegister, blockedRegister }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Tag className="h-5 w-5" />
          Job titles
        </CardTitle>
        <CardDescription>
          What the ingest stage keeps, and what it throws away.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <Label htmlFor="role_keywords" className="flex items-center gap-1.5">
            <Tag className="h-3.5 w-3.5" />
            Keep — preferred title keywords
          </Label>
          <Textarea
            id="role_keywords"
            rows={3}
            placeholder="product manager, technical program manager, product operations"
            {...roleRegister}
          />
          <p className="text-xs text-muted-foreground">
            The ingest stage drops postings whose title doesn't substring-match any of these.
            "product manager" already catches senior/staff/principal — no need to enumerate every variant.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="blocked_title_keywords" className="flex items-center gap-1.5">
            <Ban className="h-3.5 w-3.5" />
            Block — wrong-discipline title keywords
          </Label>
          <Textarea
            id="blocked_title_keywords"
            rows={2}
            placeholder="engineer, designer, data scientist, recruiter, counsel"
            {...blockedRegister}
          />
          <p className="text-xs text-muted-foreground">
            Whole-word matched: a title hitting any of these is skipped at scrape time, hidden
            in the Matches tab, and penalised in scoring. "engineer" catches "Software Engineer"
            but not "Engineering".
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
