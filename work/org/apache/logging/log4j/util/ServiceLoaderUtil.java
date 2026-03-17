package org.apache.logging.log4j.util;

import java.lang.invoke.MethodHandles;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.ServiceLoader;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Native-image friendly replacement for Log4j's ServiceLoaderUtil.
 *
 * The upstream implementation uses LambdaMetafactory / invokedynamic paths that
 * may attempt runtime class generation under GraalVM native-image. This version
 * keeps the same public API shape but performs service loading with plain Java
 * control flow only.
 */
public final class ServiceLoaderUtil {
    private ServiceLoaderUtil() {}

    public static <T> Stream<T> loadServices(Class<T> serviceClass, MethodHandles.Lookup lookup) {
        return loadServices(serviceClass, lookup, false);
    }

    public static <T> Stream<T> loadServices(Class<T> serviceClass, MethodHandles.Lookup lookup, boolean verbose) {
        return loadServices(serviceClass, lookup, verbose, true);
    }

    static <T> Stream<T> loadServices(
            Class<T> serviceClass, MethodHandles.Lookup lookup, boolean verbose, boolean useTccl) {
        ClassLoader lookupLoader = lookup.lookupClass().getClassLoader();
        List<T> result = new ArrayList<>();
        Set<Class<?>> seen = new HashSet<>();

        collectServices(result, seen, serviceClass, lookupLoader, verbose);

        if (useTccl) {
            ClassLoader tccl = Thread.currentThread().getContextClassLoader();
            if (tccl != null && tccl != lookupLoader) {
                collectServices(result, seen, serviceClass, tccl, verbose);
            }
        }

        return result.stream();
    }

    static <T> Iterable<T> callServiceLoader(
            MethodHandles.Lookup lookup, Class<T> serviceClass, ClassLoader loader, boolean verbose) {
        try {
            return ServiceLoader.load(serviceClass, loader);
        } catch (Throwable t) {
            if (verbose) {
                System.err.println("Unable to load services for service " + serviceClass + ": " + t);
                t.printStackTrace(System.err);
            }
            return Collections.emptyList();
        }
    }

    private static <T> void collectServices(
            List<T> out, Set<Class<?>> seen, Class<T> serviceClass, ClassLoader loader, boolean verbose) {
        Iterable<T> iterable = callServiceLoader(null, serviceClass, loader, verbose);
        for (T service : iterable) {
            if (service == null) {
                continue;
            }
            Class<?> implClass = service.getClass();
            if (seen.add(implClass)) {
                out.add(service);
            }
        }
    }
}