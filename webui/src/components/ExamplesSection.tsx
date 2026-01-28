import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { getExamples } from '@/utils/examples';
import { useState } from 'react';
import { ChevronRight, Sparkles } from 'lucide-react';

interface ExamplesSectionProps {
  onExampleSelect: (example: string) => void;
  disabled?: boolean;
}

const EXAMPLE_CATEGORIES = [
  {
    id: 'welcome-suggestions' as const,
    label: 'Quick Tasks',
    userType: 'mixed' as const,
    description: 'Common programming and development tasks',
  },
  {
    id: 'task-creation' as const,
    label: 'Project Ideas',
    userType: 'mixed' as const,
    description: 'Larger projects and applications to build',
  },
  {
    id: 'welcome-suggestions' as const,
    label: 'For Beginners',
    userType: 'non-technical' as const,
    description: 'Simple tasks perfect for getting started',
  },
  {
    id: 'welcome-suggestions' as const,
    label: 'Advanced Tasks',
    userType: 'technical' as const,
    description: 'Complex development and engineering tasks',
  },
];

export const ExamplesSection: React.FC<ExamplesSectionProps> = ({
  onExampleSelect,
  disabled = false,
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handleExampleClick = (example: string) => {
    onExampleSelect(example);
    setIsModalOpen(false);
  };

  return (
    <div className="space-y-4">
      {/* More Examples Button */}
      <div className="flex justify-center">
        <Dialog open={isModalOpen} onOpenChange={setIsModalOpen}>
          <DialogTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              disabled={disabled}
            >
              <Sparkles className="mr-2 h-4 w-4" />
              Examples
              <ChevronRight className="ml-1 h-3 w-3" />
            </Button>
          </DialogTrigger>

          <DialogContent className="max-h-[80vh] max-w-2xl overflow-hidden">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Sparkles className="h-5 w-5" />
                Example Tasks & Ideas
              </DialogTitle>
            </DialogHeader>

            <div className="max-h-[60vh] space-y-6 overflow-y-auto pr-2">
              {EXAMPLE_CATEGORIES.map((category) => {
                const examples = getExamples(category.id, category.userType);

                return (
                  <div
                    key={`${category.id}-${category.userType}-${category.label}`}
                    className="space-y-3"
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">
                        {category.label}
                      </Badge>
                      <p className="text-sm text-muted-foreground">{category.description}</p>
                    </div>

                    <div className="grid gap-2 sm:grid-cols-2">
                      {examples.map((example) => (
                        <Button
                          key={example}
                          variant="outline"
                          size="sm"
                          className="h-auto min-h-[2.5rem] whitespace-normal p-3 text-left text-xs hover:bg-muted/50"
                          onClick={() => handleExampleClick(example)}
                        >
                          {example}
                        </Button>
                      ))}
                    </div>
                  </div>
                );
              })}

              {/* Custom Input Suggestion */}
              <div className="space-y-2 border-t pt-4">
                <p className="text-center text-sm text-muted-foreground">
                  Or ask me anything else you'd like help with!
                </p>
                <div className="flex justify-center">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setIsModalOpen(false);
                      // Focus the input without filling it
                    }}
                  >
                    Write your own question
                  </Button>
                </div>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
};
