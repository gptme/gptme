import type { FC } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { MenuBar } from '@/components/MenuBar';
import { Button } from '@/components/ui/button';

const NotFound: FC = () => {
  const location = useLocation();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar />
      <div className="flex flex-1 items-center justify-center">
        <div className="text-center">
          <h1 className="mb-4 text-2xl font-bold">Page not found</h1>
          <p className="mb-2 text-muted-foreground">
            No page matches <code className="font-mono">{location.pathname}</code>.
          </p>
          <p className="mb-4 text-sm text-muted-foreground">
            The link may be broken, or the page may have moved.
          </p>
          <Button asChild>
            <Link to="/">Go to chat</Link>
          </Button>
        </div>
      </div>
    </div>
  );
};

export default NotFound;
