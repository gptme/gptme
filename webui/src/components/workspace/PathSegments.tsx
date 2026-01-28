import { Button } from '@/components/ui/button';
import { ChevronRight } from 'lucide-react';

interface PathSegmentsProps {
  path: string;
  workspaceRoot: string;
  onNavigate: (path: string) => void;
}

export function PathSegments({ path, workspaceRoot, onNavigate }: PathSegmentsProps) {
  const segments = path ? path.split('/') : [];

  return (
    <div className="flex items-center space-x-1 text-sm">
      <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => onNavigate('')}>
        {workspaceRoot || '/'}
      </Button>
      {segments.map((segment, index) => {
        if (!segment) return null;
        const segmentPath = segments.slice(0, index + 1).join('/');
        return (
          <div key={segmentPath} className="flex items-center">
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2"
              onClick={() => onNavigate(segmentPath)}
            >
              {segment}
            </Button>
          </div>
        );
      })}
    </div>
  );
}
