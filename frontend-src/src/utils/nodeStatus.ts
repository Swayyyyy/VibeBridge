type NodeStatusLike = {
  nodeId: string;
  status: string;
};

export type NodeConnectionState = 'online' | 'offline' | null;

export const getNodeConnectionState = (
  nodes: NodeStatusLike[],
  nodeId?: string | null,
): NodeConnectionState => {
  if (!nodeId) {
    return null;
  }

  const matchedNode = nodes.find((node) => node.nodeId === nodeId);
  if (matchedNode) {
    return matchedNode.status === 'online' ? 'online' : 'offline';
  }

  return nodes.length > 0 ? 'offline' : null;
};

export const isNodeOffline = (
  nodes: NodeStatusLike[],
  nodeId?: string | null,
): boolean => {
  return getNodeConnectionState(nodes, nodeId) === 'offline';
};
