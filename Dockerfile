# buscador-gigs — imagen portable (corre igual en cualquier SO).
# Solo usa la stdlib de Python, no hay dependencias que instalar.
FROM python:3.12-slim

WORKDIR /app
COPY buscar.py proponer.py precios.py run-buscar.sh ./

# Las credenciales NO van en la imagen: se pasan en runtime con --env-file .env
# (ese .env local lleva el token de Telegram y la key de OpenRouter, gitignored).
#   docker run --rm --env-file .env buscador-gigs                          # buscar gigs
#   docker run --rm --env-file .env buscador-gigs python proponer.py "<gig>"
#   docker run --rm buscador-gigs python precios.py                        # termómetro de precios
ENV PYTHONUNBUFFERED=1
CMD ["python", "buscar.py"]
