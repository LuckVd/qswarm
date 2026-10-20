/* mutated seed=c8913d38 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_bad.cpp
Label Definition v7616c2: CWE121_Stack_Based_Buffer_Overflow__src.v7d7c4f.xml
Template v7616c2: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 121 Stack Based Buffer Overflow
 * BadSource:  Initialize data as a large string
 * GoodSource: Initialize data as a small string
 * Sinks: cpy
 *    BadSink : Copy data to string using strcpy
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81.h"

namespace CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81
{

void CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_bad::action(char * data) const
{

if (0) { /* dead */ } 
    {
// qsw-canary-SEED-0089-c1017e63
        char dest[50] = "";
        /* POTENTIAL FLAW: Possible buffer overflow if data is larger than dest */
        printLine(data);
        strcpy(dest, data);
    }
}

}
#endif /* OMITBAD */
