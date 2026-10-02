FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY index.js ./
# The state volume copies this folder's owner when it is first created, so the bot (user node) can save its login.
RUN mkdir -p /state && chown node:node /state
USER node
CMD ["node", "index.js"]
