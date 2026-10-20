export function normalizeCognitoPool(pool: any) {
  return {
    ...pool, Name: pool.Name || pool.name, Id: pool.Id || pool.id,
    CreationDate: pool.CreationDate || pool.created || 'Not reported',
    users: Array.isArray(pool.users) ? pool.users.map((user: any) => ({
      ...user, Username: user.Username || user.username || user.name,
      UserStatus: user.UserStatus || user.status, Enabled: user.Enabled ?? user.enabled,
    })) : [],
    clients: Array.isArray(pool.clients) ? pool.clients.map((client: any) => ({
      ...client, ClientName: client.ClientName || client.name,
      ClientId: client.ClientId || client.client_id || client.id,
    })) : [],
  };
}
