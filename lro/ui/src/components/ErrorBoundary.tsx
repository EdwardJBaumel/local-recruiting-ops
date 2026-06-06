import { Component, type ErrorInfo, type ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";

/**
 * Top-level error boundary. Without this, any runtime error in any
 * component unwinds the whole tree and React renders a blank page —
 * which is exactly what was happening when the location-filter code
 * choked on an unexpected config shape.
 *
 * With this in place: the same crash now renders a card showing the
 * error message + stack, plus a "Retry" button that resets the
 * boundary's state. Users can SEE what broke instead of a blank page.
 *
 * Why a class component: React's error-boundary API still requires
 * `componentDidCatch`/`getDerivedStateFromError` — there's no hook
 * equivalent. Just one of those places where the modern functional
 * style doesn't apply yet.
 */
interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log to console so DevTools can show the full stack — the
    // rendered card only shows the user-friendly summary.
    console.error("[ErrorBoundary] Caught render error:", error, info);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="container py-12 max-w-2xl">
          <Card className="border-destructive/50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-destructive">
                <AlertTriangle className="h-5 w-5" />
                Something broke while rendering
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Local Recruiting Ops caught a runtime error and stopped rendering to avoid going blank. The full stack is in
                your browser DevTools console (F12).
              </p>
              <pre className="text-xs font-mono p-3 bg-secondary rounded-md overflow-x-auto whitespace-pre-wrap">
                {this.state.error.message}
              </pre>
              <Button onClick={this.reset} variant="outline" size="sm">
                Retry
              </Button>
            </CardContent>
          </Card>
        </div>
      );
    }
    return this.props.children;
  }
}
