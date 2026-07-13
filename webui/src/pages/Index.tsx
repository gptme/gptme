import { type FC } from 'react';
import { useParams } from 'react-router-dom';
import { MenuBar } from '@/components/MenuBar';
import MainLayout from '@/components/MainLayout';
import { decodeRouteParam } from '@/utils/routes';

interface Props {
  className?: string;
}

const Index: FC<Props> = () => {
  const { id } = useParams<{ id?: string }>();
  const conversationId = decodeRouteParam(id);

  return (
    <div className="flex h-dvh flex-col">
      <MenuBar />
      <MainLayout conversationId={conversationId} />
    </div>
  );
};

export default Index;
