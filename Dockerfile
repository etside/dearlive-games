# Teen Patti Pro provider API — one image, one process.
#
# The HTTP API and the WebSocket gateway run in the SAME process on purpose:
# a separate socket process would hold a second in-memory game state.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

WORKDIR /app

COPY pyproject.toml .python-version README.md ./
COPY common/ common/
COPY games/ games/
COPY provider/ provider/
COPY integrations/ integrations/
COPY staging/ staging/
COPY admin/ admin/
COPY assets/ assets/
COPY db/ db/
COPY docs/ docs/
COPY tools/ tools/
COPY sdk/ sdk/
COPY index.html index.html
COPY vercel.json .vercelignore ./

RUN useradd --create-home --uid 10001 provider \
    && chown -R provider:provider /app
USER provider

EXPOSE 5002 5003

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5002/api/v1/provider/health',timeout=4).status==200 else 1)"

# --confirmed flips the TBC business sign-off. Without it a production boot is
# refused, so real money can never run on unconfirmed rules.
CMD ["python", "-m", "games.teen_patti_pro.api", \
     "--host", "0.0.0.0", "--port", "5002", "--ws-port", "5003", "--confirmed"]
