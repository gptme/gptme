import { type FC } from 'react';
import { useParams } from 'react-router-dom';
import { MenuBar } from '@/components/MenuBar';
import MainLayout from '@/components/MainLayout';

interface Props {
  className?: string;
}

const Index: FC<Props> = () => {
  const { id } = useParams<{ id?: string }>();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar showRightSidebar={!!id} />
      <MainLayout conversationId={id} />
    </div>
  );
};

export default Index;
