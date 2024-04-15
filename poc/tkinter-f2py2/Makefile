# A simple makefile using f2py

all: carrotcake.so

carrotcake.so: model.f90
	f2py -c -m carrotcake $^

.PHONY:		all clean spotless

clean: spotless
spotless:
		\rm -f *.so
