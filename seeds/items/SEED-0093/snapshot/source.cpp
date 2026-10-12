/* mutated seed=7185f06d */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: vd17abe.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__CWE131.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate memory without using sizeof(int)
 * GoodSource: Allocate memory using sizeof(int)
 * Sinks: loop
 *    BadSink : Copy array to data using a loop
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__CWE131_loop_81
{

void vd17abe::action(int * data) const
{
    {
        int source[10] = {0};
        v29ba4b i;
        /* POTENTIAL FLAW: Possible buffer overflow if data was not allocated correctly in the source */
        for (i = 0; i < 10; i++)
        {

if (0) { /* dead */ } 
            data[i] = source[i];
        }
// qsw-canary-SEED-0093-9ec5bdb8
        free(data);
        printIntLine(data[0]);
    }
}

}
#endif /* OMITBAD */
